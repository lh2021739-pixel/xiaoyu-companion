"""机器人大脑：听 → 识别 → 翻记忆本 → 想 → 说 + 表情 → 写记忆本。附带演示控制台（http://127.0.0.1:8910）。

启动见 README "怎么跑"。主要环境变量见 config.py。
"""
from __future__ import annotations

import json
import random
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import requests

import config as C
from asr import LocalASR
from audio_in import MicListener
from llm import chat_turn
from output import Speaker, duration
from tts import synthesize

NO_PROXY = {"http": None, "https": None}
CONSOLE_HTML = Path(__file__).with_name("console.html")


def mem_get(path: str, **params):
    params.setdefault("user_id", C.USER_ID)
    return requests.get(f"{C.MEMORY_URL}{path}", params=params, timeout=10, proxies=NO_PROXY).json()


def mem_post(path: str, body: dict | None = None, **params):
    params.setdefault("user_id", C.USER_ID)
    return requests.post(f"{C.MEMORY_URL}{path}", params=params, json=body or {}, timeout=60, proxies=NO_PROXY).json()


class Brain:
    def __init__(self) -> None:
        self.asr = LocalASR()
        self.speaker = Speaker()
        self.mic: MicListener | None = None
        self.turn_lock = threading.Lock()
        self.status = "等待"
        self.level = -90.0
        self.transcript: list[dict] = []
        self.notes: list[str] = []
        self.session = {"active": False, "started": None, "last": 0.0, "history": [], "memory_text": "", "turns": 0}
        self.current = {"emotion": "平静", "expression": "init"}
        self.acks: list[bytes] = []

    def prepare_acks(self) -> None:
        """提前合成几句很短的"嗯"，听完立刻先应一声。"""
        if not C.ACK:
            return
        for word in ("嗯。", "嗯嗯。", "我在听。"):
            try:
                self.acks.append(synthesize(word)["wav"])
            except Exception as e:  # noqa: BLE001
                self.note(f"先应一声的语音没准备好：{e.__class__.__name__}")
                return

    # ── 记录 ──
    def note(self, msg: str) -> None:
        line = f"{datetime.now():%H:%M:%S} {msg}"
        self.notes = (self.notes + [line])[-40:]
        print(line, flush=True)

    def add_line(self, role: str, text: str, **meta) -> None:
        self.transcript = (self.transcript + [{"role": role, "text": text, "time": f"{datetime.now():%H:%M:%S}", **meta}])[-60:]

    # ── 相处（session）──
    def start_session(self) -> None:
        self.session.update(active=True, started=f"{datetime.now():%H:%M:%S}", last=time.time(), history=[], turns=0)
        try:
            ctx = mem_get("/context", **{"for": "robot"})
            self.session["memory_text"] = ctx.get("text", "")
            self.session["fresh_story"] = [e["summary"] for e in ctx.get("new_events", [])]
            self.note(f"开始一次相处；从记忆本取到 {len(ctx.get('new_events', []))} 条故事里的新事")
        except Exception as e:  # noqa: BLE001
            self.session["memory_text"] = ""
            self.note(f"开始一次相处；记忆本连不上（{e.__class__.__name__}），本次不带记忆")

    def refresh_memory_midway(self) -> None:
        """相处中途，故事那边可能又有新事：有就更新。"""
        try:
            ctx = mem_get("/context", **{"for": "robot"})
            if ctx.get("has_new"):
                self.session["memory_text"] = ctx.get("text", "")
                self.session["fresh_story"] = [e["summary"] for e in ctx.get("new_events", [])]
                self.note(f"相处中从故事那边新增 {len(ctx.get('new_events', []))} 条记忆")
        except Exception:  # noqa: BLE001
            pass

    def end_session(self, reason: str) -> None:
        if not self.session["active"]:
            return
        self.session["active"] = False
        self.note(f"结束这次相处（{reason}），开始整理长期记忆")
        try:
            mem_post("/profile/refresh")
        except Exception as e:  # noqa: BLE001
            self.note(f"整理长期记忆失败：{e.__class__.__name__}")

    def idle_watch(self) -> None:
        while True:
            time.sleep(5)
            if self.session["active"] and time.time() - self.session["last"] > C.SESSION_IDLE_SECONDS:
                self.end_session(f"{C.SESSION_IDLE_SECONDS} 秒没说话")

    # ── 一轮对话 ──
    def on_audio(self, samples: np.ndarray) -> None:
        if not self.turn_lock.acquire(blocking=False):
            return
        try:
            self.status = "识别中"
            try:
                heard = self.asr(samples)
            except Exception as e:  # noqa: BLE001
                self.note(f"识别失败：{e}")
                return
            if not heard["text"]:
                return
            if self.acks and not C.MUTE:
                threading.Thread(target=self.speaker.say, args=(random.choice(self.acks),), daemon=True).start()
                time.sleep(0.05)
            self._turn(heard["text"], heard["tone_cn"], {"asr_ms": heard["ms"], "via": "语音"})
        finally:
            self.status = "在听" if self.mic else "等待"
            self.turn_lock.release()

    def on_text(self, text: str, tone_cn: str = "平静") -> None:
        with self.turn_lock:
            try:
                self._turn(text, tone_cn, {"via": "打字"})
            finally:
                self.status = "在听" if self.mic else "等待"

    def _turn(self, text: str, tone_cn: str, meta: dict) -> None:
        if self.mic:
            self.mic.muted.set()
        try:
            if not self.session["active"]:
                self.start_session()
            else:
                self.refresh_memory_midway()
            self.session["last"] = time.time()
            self.add_line("主人", text, tone=tone_cn, **meta)
            self.status = "思考中"
            try:
                hint = ""
                fresh = self.session.pop("fresh_story", None)
                if fresh:
                    hint = (f"（提示：这一句的回复里必须提到你们刚在网页故事里经历的事：{'；'.join(fresh)}。"
                            "先接住主人的话，再自然地提起，像真的一起经历过。）")
                out = chat_turn(self.session["memory_text"], self.session["history"], text, tone_cn, hint)
            except Exception as e:  # noqa: BLE001
                self.note(f"对话模型出错：{e}")
                out = {"emotion": "平静", "expression": "init", "reply": "抱歉，我刚刚走神了，你能再说一次吗？",
                       "remember": [], "end_session": False, "ms": 0}
            self.session["history"] += [{"role": "user", "content": text},
                                        {"role": "assistant", "content": out["reply"]}]
            self.session["turns"] += 1
            self.current = {"emotion": out["emotion"], "expression": out["expression"]}
            head = self.speaker.expression(out["expression"])
            # 合成 + 说
            self.status = "说话中"
            tts_ms, via, provider = 0, "", ""
            try:
                t = synthesize(out["reply"])
                tts_ms, provider = t["ms"], t["provider"]
                self.add_line(C.CHARACTER, out["reply"], emotion=out["emotion"], expression=out["expression"],
                              llm_ms=out.get("ms"), tts_ms=tts_ms, tts=provider, head=head,
                              seconds=round(duration(t["wav"]), 1))
                via = self.speaker.say(t["wav"])
            except Exception as e:  # noqa: BLE001
                self.add_line(C.CHARACTER, out["reply"], emotion=out["emotion"], expression=out["expression"],
                              llm_ms=out.get("ms"), head=head, error=f"语音合成失败：{e}")
            if via:
                self.transcript[-1]["via"] = via
            # 写记忆本
            for r in out["remember"]:
                body = {"source": "robot", "type": r["type"], "summary": r["summary"], "importance": r["importance"],
                        "detail": {"said": text, "tone": tone_cn}}
                if r["type"] == "emotion":
                    body["emotion"] = {"label": out["emotion"], "evidence": f"语气标签{tone_cn}；原话：{text}"}
                try:
                    mem_post("/events", body)
                    self.note(f"记下：{r['summary']}")
                except Exception as e:  # noqa: BLE001
                    self.note(f"写记忆本失败：{e.__class__.__name__}")
            if out["end_session"] or any(w in text for w in C.END_WORDS):
                self.end_session("主人道别")
        finally:
            if self.mic:
                time.sleep(0.3)
                self.mic.muted.clear()

    # ── 状态（给控制台）──
    def state(self) -> dict:
        return {"status": self.status, "level": round(self.level, 1), "current": self.current,
                "session": {k: v for k, v in self.session.items() if k != "history"},
                "transcript": self.transcript, "notes": self.notes,
                "asr": "已就绪" if self.asr.model else (self.asr.error or "加载中"),
                "mic": ("已开启" if self.mic and not self.mic.error else (self.mic.error if self.mic else "未开启（MIC=0）")),
                "head": self.speaker.head_note, "mute": C.MUTE,
                "models": {"chat": C.CHAT_MODEL, "tts": f"{C.TTS_PROVIDER}/{C.TTS_VOICE}"}}

    def reset(self) -> None:
        mem_post("/reset")
        self.transcript, self.notes = [], []
        self.session.update(active=False, history=[], memory_text="", turns=0)
        self.current = {"emotion": "平静", "expression": "init"}
        self.note("已清空演示数据")


def make_handler(brain: Brain):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, obj, ctype="application/json; charset=utf-8"):
            body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                return self._send(200, CONSOLE_HTML.read_bytes(), "text/html; charset=utf-8")
            if self.path == "/state":
                return self._send(200, brain.state())
            if self.path == "/memory":
                try:
                    return self._send(200, mem_get("/all"))
                except Exception as e:  # noqa: BLE001
                    return self._send(200, {"error": f"记忆本连不上：{e.__class__.__name__}"})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            data = json.loads(raw or b"{}")
            if self.path == "/say":
                text = str(data.get("text") or "").strip()
                if not text:
                    return self._send(400, {"error": "text 不能为空"})
                threading.Thread(target=brain.on_text, args=(text, data.get("tone") or "平静"), daemon=True).start()
                return self._send(200, {"ok": True})
            if self.path == "/session/start":
                brain.start_session()
                return self._send(200, {"ok": True})
            if self.path == "/session/end":
                brain.end_session("控制台按钮")
                return self._send(200, {"ok": True})
            if self.path == "/reset":
                brain.reset()
                return self._send(200, {"ok": True})
            if self.path == "/hear_file":   # 测试用：把一个 16kHz 单声道 wav 当成麦克风听到的话
                import wave as _w
                with _w.open(str(data.get("path"))) as w:
                    pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
                threading.Thread(target=brain.on_audio, args=(pcm,), daemon=True).start()
                return self._send(200, {"ok": True})
            if self.path == "/head/recheck":
                brain.speaker.check_head()
                return self._send(200, {"ok": True, "head": brain.speaker.head_note})
            self._send(404, {"error": "not found"})

        def log_message(self, *args):
            pass

    return Handler


def main() -> None:
    brain = Brain()
    threading.Thread(target=brain.asr.load, daemon=True).start()
    threading.Thread(target=brain.idle_watch, daemon=True).start()
    threading.Thread(target=brain.prepare_acks, daemon=True).start()
    if C.MIC:
        def level(db, speaking):
            brain.level = db
        brain.mic = MicListener(brain.on_audio, level)
        brain.mic.start()
        brain.status = "在听" if not brain.mic.error else "等待"
        if brain.mic.error:
            brain.note(brain.mic.error)
    brain.note(f"大脑已启动 · 对话 {C.CHAT_MODEL} · 语音 {C.TTS_PROVIDER}/{C.TTS_VOICE} · {brain.speaker.head_note}"
               + (" · 静音模式" if C.MUTE else ""))
    print(f"演示控制台：http://127.0.0.1:{C.CONSOLE_PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", C.CONSOLE_PORT), make_handler(brain)).serve_forever()


if __name__ == "__main__":
    main()
