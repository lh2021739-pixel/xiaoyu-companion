"""机器人大脑：听 → 识别 → 读当前角色的人设和记忆 → 想 → 说 + 表情 → 写回记忆库。附带演示控制台（http://127.0.0.1:8910）。

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

NO_PROXY = {"http": None, "https": None, "all": None}   # 本机请求不走任何代理（包括 all_proxy 里的 SOCKS 代理）
CONSOLE_HTML = Path(__file__).with_name("console.html")
AVATAR_DIR = Path(__file__).with_name("avatars")
# 控制台可以手动触发的头部动作（名字 → 按钮上的中文）
HEAD_ACTIONS = {"smile": "微笑", "sad": "难过", "makeface": "鬼脸", "kiss": "亲亲", "blink": "眨眼",
                "blink_left": "眨左眼", "blink_right": "眨右眼", "eyes_rotate": "转眼睛", "turn_neck": "转头", "init": "复位"}
FACE_EXPRESSIONS = {"smile", "sad", "makeface", "kiss", "blink", "init"}   # 这些同时更新控制台上的表情


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
        self.session = {"active": False, "started": None, "last": 0.0, "history": [], "turns": 0,
                        "persona_text": "", "memory_text": "", "character": None}
        self.current = {"emotion": "平静", "expression": "init"}
        self.acks: dict[str, list[bytes]] = {}

    @property
    def char_name(self) -> str:
        return (self.session.get("character") or {}).get("name") or "角色"

    @property
    def voice_cfg(self) -> dict | None:
        return (self.session.get("character") or {}).get("voice")

    def prepare_acks(self, voice_cfg: dict | None = None) -> None:
        """提前合成几句很短的"嗯"（按角色的音色），听完立刻先应一声。"""
        key = json.dumps(voice_cfg or {}, sort_keys=True)
        if not C.ACK or key in self.acks:
            return
        wavs = []
        for word in ("嗯。", "嗯嗯。", "我在听。"):
            try:
                wavs.append(synthesize(word, voice_cfg)["wav"])
            except Exception as e:  # noqa: BLE001
                self.note(f"先应一声的语音没准备好：{e.__class__.__name__}")
                return
        self.acks[key] = wavs

    # ── 记录 ──
    def note(self, msg: str) -> None:
        line = f"{datetime.now():%H:%M:%S} {msg}"
        self.notes = (self.notes + [line])[-40:]
        print(line, flush=True)

    def add_line(self, role: str, text: str, **meta) -> None:
        self.transcript = (self.transcript + [{"role": role, "text": text, "time": f"{datetime.now():%H:%M:%S}", **meta}])[-60:]

    # ── 相处（session）──
    def start_session(self) -> None:
        """开始一次相处：从记忆库读"当前角色"的人设和全部记忆。"""
        self.session.update(active=True, started=f"{datetime.now():%H:%M:%S}", last=time.time(), history=[], turns=0,
                            first_turn=True)
        try:
            ctx = mem_get("/context", **{"for": "robot"})
            self.session.update(persona_text=ctx.get("persona_text", ""), memory_text=ctx.get("memory_text", ""),
                                character=ctx.get("character"))
            if ctx.get("has_character"):
                n = ctx["memory_text"].count("\n- ")
                self.note(f"开始一次相处：当前角色「{self.char_name}」，读到约 {n} 条记忆")
                threading.Thread(target=self.prepare_acks, args=(self.voice_cfg,), daemon=True).start()
            else:
                self.note("开始一次相处：记忆库里还没有角色，请先导入")
        except Exception as e:  # noqa: BLE001
            self.session.update(persona_text="", memory_text="", character=None)
            self.note(f"开始一次相处：记忆库连不上（{e.__class__.__name__}），本次没有人设和记忆")

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
            acks = self.acks.get(json.dumps(self.voice_cfg or {}, sort_keys=True))
            if acks and not C.MUTE:
                threading.Thread(target=self.speaker.say, args=(random.choice(acks),), daemon=True).start()
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
            self.session["last"] = time.time()
            self.add_line("你", text, tone=tone_cn, **meta)
            self.status = "思考中"
            try:
                hint = ""
                if self.session.pop("first_turn", False) and self.session.get("character"):
                    shared = [l[2:] for l in self.session["memory_text"].split("【后来")[0].splitlines() if l.startswith("- ")]
                    warm = [s for s in shared if not any(w in s for w in C.SAD_EVENT_WORDS)] or shared   # 开场挑温暖的
                    if warm:
                        hint = ("（提示：这是你们这次相处的第一句。先接住他，回复里自然地提到一件你们共同经历过的具体小事，"
                                "只用记忆里写到的细节，不要自己添加情节、动作或原话；记忆里的'你'是你自己、'他'是他，"
                                "说出口时要换成你对他说话的口吻，不要照着念。"
                                f"比如这件：{random.choice(warm)}）")
                if any(w in text for w in C.CRISIS_WORDS):
                    hint = C.CRISIS_HINT
                    self.note("听到轻生相关的话：已要求她劝他找家人朋友和专业帮助")
                out = chat_turn(self.session["persona_text"], self.session["memory_text"], self.session["history"],
                                text, tone_cn, hint)
            except Exception as e:  # noqa: BLE001
                self.note(f"对话模型出错：{e}")
                out = {"emotion": "平静", "expression": "init", "reply": "抱歉，我刚刚走神了，你能再说一次吗？",
                       "remember": [], "end_session": False, "ms": 0}
            if any(w in text for w in C.CRISIS_WORDS):
                # 兜底：她的回答里没劝他求助，就补上固定的一句（模型只靠提示做不到每次都说）
                if not any(w in out["reply"] for w in ("家人", "朋友", "热线", "医生", "医院")):
                    out["reply"] = out["reply"].rstrip("。！？!?.…") + "。" + C.CRISIS_LINE
                out["emotion"], out["expression"], out["end_session"] = "难过", "sad", False
            self.session["history"] += [{"role": "user", "content": text},
                                        {"role": "assistant", "content": out["reply"]}]
            self.session["turns"] += 1
            self.current = {"emotion": out["emotion"], "expression": out["expression"]}
            head = self.speaker.expression(out["expression"])
            # 合成 + 说
            self.status = "说话中"
            tts_ms, via, provider = 0, "", ""
            try:
                t = synthesize(out["reply"], self.voice_cfg)
                tts_ms, provider = t["ms"], t["provider"]
                self.add_line(self.char_name, out["reply"], emotion=out["emotion"], expression=out["expression"],
                              llm_ms=out.get("ms"), tts_ms=tts_ms, tts=provider, head=head,
                              seconds=round(duration(t["wav"]), 1))
                via = self.speaker.say(t["wav"])
            except Exception as e:  # noqa: BLE001
                self.add_line(self.char_name, out["reply"], emotion=out["emotion"], expression=out["expression"],
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
                self.end_session("道别")
        finally:
            if self.mic:
                time.sleep(0.3)
                self.mic.unmute()

    # ── 状态（给控制台）──
    def state(self) -> dict:
        return {"status": self.status, "level": round(self.level, 1), "current": self.current,
                "session": {k: v for k, v in self.session.items() if k != "history"},
                "transcript": self.transcript, "notes": self.notes,
                "asr": "已就绪" if self.asr.model else (self.asr.error or "加载中"),
                "mic": (f"已开启（{self.mic.label()}）" if self.mic and not self.mic.error else (self.mic.error if self.mic else "未开启（MIC=0）")),
                "head": self.speaker.head_note, "mute": C.MUTE,
                "character": {k: (self.session.get("character") or {}).get(k) for k in ("id", "name")},
                "models": {"chat": C.CHAT_MODEL, "tts": self._tts_label()}}

    def _tts_label(self) -> str:
        v = self.voice_cfg or {}
        prov = v.get("provider") or C.TTS_PROVIDER
        voice = v.get("voice") or (C.TTS_DASHSCOPE_VOICE if prov == "dashscope" else C.TTS_STEPFUN_VOICE)
        return f"{'百炼' if prov == 'dashscope' else '阶跃'}/{voice}"

    def reset(self) -> None:
        """清空当前角色的聊天记忆（保留导入的经历）。"""
        mem_post("/reset", scope="chat")
        self.transcript, self.notes = [], []
        self.session.update(active=False, history=[], turns=0)
        self.current = {"emotion": "平静", "expression": "init"}
        self.note("已清空和当前角色的聊天记忆（导入的经历保留）")

    def switch_character(self, character_id: str) -> dict:
        res = mem_post("/characters/activate", {"character_id": character_id})
        if res.get("ok"):
            self.end_session("切换角色")
            self.session.update(character=None, persona_text="", memory_text="")
            self.transcript = []
            self.note(f"已切换当前角色：{character_id}，下一句话开始用新人设")
        return res


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
            if self.path.startswith("/avatar/"):
                # 角色照片：brain/avatars/<角色id>.jpg|png|webp，没有就 404（控制台改显示表情脸）
                cid = "".join(ch for ch in self.path[len("/avatar/"):].split("?")[0] if ch.isalnum() or ch in "_-")
                for ext, mime in ((".jpg", "image/jpeg"), (".png", "image/png"), (".webp", "image/webp")):
                    f = AVATAR_DIR / f"{cid}{ext}"
                    if cid and f.exists():
                        return self._send(200, f.read_bytes(), mime)
                return self._send(404, {"error": "没有照片"})
            if self.path == "/characters":
                try:
                    return self._send(200, mem_get("/characters"))
                except Exception as e:  # noqa: BLE001
                    return self._send(200, {"characters": [], "error": f"记忆库连不上：{e.__class__.__name__}"})
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
            if self.path == "/character/activate":
                return self._send(200, brain.switch_character(str(data.get("character_id") or "")))
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
            if self.path == "/head/action":
                # 控制台手动让机器人做一个动作（只放行头部控制程序支持、且不需要参数的动作）
                name = str(data.get("action") or "")
                if name not in HEAD_ACTIONS:
                    return self._send(400, {"error": f"不支持的动作：{name}"})
                result = brain.speaker.expression(name)
                if name in FACE_EXPRESSIONS:
                    brain.current = {**brain.current, "expression": name}
                brain.note(f"手动动作：{HEAD_ACTIONS[name]} · {result}")
                return self._send(200, {"ok": result == "已发给机器人头", "result": result})
            self._send(404, {"error": "not found"})

        def log_message(self, *args):
            pass

    return Handler


def main() -> None:
    brain = Brain()
    threading.Thread(target=brain.asr.load, daemon=True).start()
    threading.Thread(target=brain.idle_watch, daemon=True).start()
    threading.Thread(target=brain.prepare_acks, args=(None,), daemon=True).start()
    if C.MIC:
        def level(db, speaking):
            brain.level = float(db)
        brain.mic = MicListener(brain.on_audio, level, source=C.MIC_SOURCE,
                                head_serial=C.HEAD_ADB_SERIAL, on_note=brain.note)
        brain.mic.start()
        brain.status = "在听" if not brain.mic.error else "等待"
        if brain.mic.error:
            brain.note(brain.mic.error)
    brain.note(f"大脑已启动 · 对话 {C.CHAT_MODEL} · 语音 {brain._tts_label()} · {brain.speaker.head_note}"
               + (" · 静音模式" if C.MUTE else ""))
    print(f"演示控制台：http://127.0.0.1:{C.CONSOLE_PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", C.CONSOLE_PORT), make_handler(brain)).serve_forever()


if __name__ == "__main__":
    main()
