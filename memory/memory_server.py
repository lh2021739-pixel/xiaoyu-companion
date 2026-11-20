"""记忆本（真）：故事和机器人共用的记忆服务。

启动：python3 memory/memory_server.py            （只用 Python 自带库；端口 8900）
数据：存在 memory_data/<user_id>/ 下（events.jsonl、profile.json、cursors.json）
整理长期记忆（画像）需要环境变量 DASHSCOPE_API_KEY；没有也能跑，只是不整理画像。

接口（和 tools/mock_memory_server.py 一致，见 docs/记忆本格式.md）：
  POST /events                       写一条事件
  GET  /context?for=story|robot      取给自己看的上下文（对方那边的新事件 + 长期记忆），取后自动标为已读；加 &peek=1 只看不标
  GET  /all                          全部事件和画像（演示大屏用）
  POST /profile/refresh[?wait=1]     把新事件整理进画像（一次相处 / 一章故事结束时调用）
  POST /reset                        清空演示数据
  GET  /health
所有接口都可带 ?user_id=xxx，默认 demo；每个用户的数据单独存放。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("MEMORY_DATA", ROOT / "memory_data"))
PORT = int(os.environ.get("MEMORY_PORT", "8900"))
LLM_URL = os.environ.get("MEMORY_LLM_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
LLM_MODEL = os.environ.get("MEMORY_LLM_MODEL", "qwen-plus")
LLM_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
CN = timezone(timedelta(hours=8))
SOURCES = {"story", "robot"}
TYPES = {"choice", "story_event", "said", "emotion"}
LOCK = threading.RLock()
CHAR = os.environ.get("CHARACTER_NAME", "小屿")


def now_iso() -> str:
    return datetime.now(CN).isoformat(timespec="seconds")


def safe_user(user_id: str) -> str:
    user_id = (user_id or "demo").strip()
    return user_id if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", user_id) else "demo"


def user_dir(user_id: str) -> Path:
    d = DATA / safe_user(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def empty_profile(user_id: str) -> dict:
    return {"user_id": user_id, "user": {"call_name": "主人", "facts": [], "likes": []},
            "promises": [], "relationship": "", "merged_upto": 0, "updated_at": None}


def load_events(user_id: str) -> list[dict]:
    p = user_dir(user_id) / "events.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_json(user_id: str, name: str, default):
    p = user_dir(user_id) / name
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(user_id: str, name: str, obj) -> None:
    p = user_dir(user_id) / name
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


# ── 写事件 ──────────────────────────────────────────────────────────────
def add_event(data: dict, user_id: str) -> dict:
    source, etype = data.get("source"), data.get("type")
    summary = str(data.get("summary") or "").strip()
    if source not in SOURCES:
        raise ValueError("source 必须是 story 或 robot")
    if etype not in TYPES:
        raise ValueError("type 必须是 choice / story_event / said / emotion")
    if not summary:
        raise ValueError("summary 不能为空")
    with LOCK:
        events = load_events(user_id)
        norm = lambda x: re.sub(r"[，。！？、,.!?\s特别非常很]", "", x)
        for old in events[-20:]:
            if old["source"] == source and old["type"] == etype and \
                    SequenceMatcher(None, norm(old["summary"]), norm(summary)).ratio() > 0.85:
                print(f"[记忆] 跳过重复：{summary}", flush=True)
                return {**old, "duplicate": True}
        event = {
            "id": f"evt_{datetime.now(CN).strftime('%Y%m%d_%H%M%S')}_{len(events) + 1}",
            "user_id": user_id,
            "time": data.get("time") or now_iso(),
            "source": source,
            "type": etype,
            "summary": summary[:300],
            "detail": data.get("detail"),
            "emotion": data.get("emotion"),
            "importance": int(data.get("importance") or (3 if etype == "emotion" else 1)),
        }
        with (user_dir(user_id) / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    print(f"[记忆] +{source}/{etype}: {summary}", flush=True)
    return event


# ── 取上下文 ────────────────────────────────────────────────────────────
def _emotion_note(e: dict) -> str:
    emo = e.get("emotion") or {}
    label = emo.get("label") if isinstance(emo, dict) else None
    return f"（情绪：{label}）" if label else ""


def _long_term_lines(profile: dict) -> list[str]:
    lines = []
    for p in profile.get("promises", []):
        if p.get("status") != "已兑现":
            lines.append(f"- 约定：{p.get('text')}（{p.get('status') or '未兑现'}）")
    for x in profile.get("user", {}).get("likes", [])[-5:]:
        lines.append(f"- 主人的喜好：{x.get('text')}")
    for x in profile.get("user", {}).get("facts", [])[-5:]:
        lines.append(f"- 关于主人：{x.get('text')}")
    if profile.get("relationship"):
        lines.append(f"- 你们的关系：{profile['relationship']}")
    return lines


def build_context(side: str, user_id: str, peek: bool) -> dict:
    other = "robot" if side == "story" else "story"
    with LOCK:
        events = load_events(user_id)
        cursors = load_json(user_id, "cursors.json", {"story": 0, "robot": 0})
        start = int(cursors.get(side, 0))
        new = [e for e in events[start:] if e["source"] == other]
        new = sorted(new, key=lambda e: (-e.get("importance", 1), e["time"]))[:8]
        new.sort(key=lambda e: e["time"])
        if not peek:
            cursors[side] = len(events)
            save_json(user_id, "cursors.json", cursors)
        profile = load_json(user_id, "profile.json", empty_profile(user_id))

    lines: list[str] = []
    if side == "story":
        if new:
            lines.append(f"【现实中刚发生的事 · 来自玩家桌上的陪伴机器人（同一个{CHAR}的记忆）】")
            lines += [f"- {e['summary']}{_emotion_note(e)}" for e in new]
        long_term = _long_term_lines(profile)
        if long_term:
            lines.append("【一直记得的】")
            lines += long_term
        if new:
            lines.append(f"本场要让{CHAR}自然地记得并回应这些事（先回应主人的情绪），不要生硬复述。")
        elif lines:
            lines.append("这些是背景记忆，没有新的事，不要反复提起旧事。")
    else:
        if new:
            lines.append(f"【网页故事里刚发生的事（那也是你，{CHAR}）】")
            lines += [f"- {e['summary']}" for e in new]
        long_term = _long_term_lines(profile)
        if long_term:
            lines.append("【你一直记得的】")
            lines += long_term
        recent_emotions = [e for e in events if e["type"] == "emotion"][-3:]
        if recent_emotions:
            lines.append("【主人最近的情绪时刻】")
            lines += [f"- {e['summary']}{_emotion_note(e)}" for e in recent_emotions]
        if new:
            lines.append("开口时自然地提起故事里刚发生的事，像真的一起经历过一样，不要生硬复述。")
    return {"has_new": bool(new), "text": "\n".join(lines), "new_events": new,
            "cursor": len(events) if not peek else start}


# ── 整理画像（长期记忆）───────────────────────────────────────────────
PROFILE_PROMPT = """你在维护一个陪伴机器人"{char}"对主人的长期记忆。
根据【新事件】更新【当前画像】，只输出更新后的完整 JSON，不要任何解释。
规则：
- 只写事件里明确出现的信息，不要编造。
- facts：关于主人的事实（工作、处境、重要的人等），likes：主人的喜好；每条一句话，去重，保留旧的。
- promises：主人和{char}之间的约定（例如一起去做某事），status 为"未兑现"或"已兑现"；事件里说兑现了才改成"已兑现"。
- relationship：一句话描述两人现在的关系和最近的状态。
- 每条 facts/likes/promises 都带 source（story 或 robot，按事件来源）。
JSON 结构：{{"user":{{"call_name":"主人","facts":[{{"text":"","source":""}}],"likes":[{{"text":"","source":""}}]}},"promises":[{{"text":"","source":"","status":"未兑现"}}],"relationship":""}}

【当前画像】
{profile}

【新事件】
{events}"""


def _llm_json(prompt: str) -> dict:
    body = {"model": LLM_MODEL, "response_format": {"type": "json_object"}, "temperature": 0.2,
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(LLM_URL, json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": f"Bearer {LLM_KEY}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        content = json.loads(r.read())["choices"][0]["message"]["content"]
    return json.loads(content)


def refresh_profile(user_id: str) -> dict:
    with LOCK:
        events = load_events(user_id)
        profile = load_json(user_id, "profile.json", empty_profile(user_id))
        start = int(profile.get("merged_upto", 0))
        new = events[start:]
    if not new:
        return {"ok": True, "merged": 0, "profile": profile}
    if not LLM_KEY:
        return {"ok": False, "merged": 0, "error": "没有 DASHSCOPE_API_KEY，跳过画像整理", "profile": profile}
    shown = {k: profile.get(k) for k in ("user", "promises", "relationship")}
    ev_lines = "\n".join(f"- [{e['source']}/{e['type']}] {e['summary']}{_emotion_note(e)}" for e in new)
    t0 = time.time()
    updated = _llm_json(PROFILE_PROMPT.format(char=CHAR, profile=json.dumps(shown, ensure_ascii=False), events=ev_lines))
    with LOCK:
        profile = load_json(user_id, "profile.json", empty_profile(user_id))
        for key in ("user", "promises", "relationship"):
            if key in updated:
                profile[key] = updated[key]
        profile["user"].setdefault("call_name", "主人")
        profile["merged_upto"] = start + len(new)
        profile["updated_at"] = now_iso()
        save_json(user_id, "profile.json", profile)
    print(f"[记忆] 画像已整理 {len(new)} 条事件，用时 {time.time() - t0:.1f}s", flush=True)
    return {"ok": True, "merged": len(new), "profile": profile}


def reset_user(user_id: str) -> None:
    with LOCK:
        d = user_dir(user_id)
        for name in ("events.jsonl", "profile.json", "cursors.json"):
            (d / name).unlink(missing_ok=True)
    print(f"[记忆] 已清空 {user_id} 的演示数据", flush=True)


# ── HTTP ───────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _query(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        return url.path, q, safe_user(q.get("user_id", "demo"))

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        path, q, uid = self._query()
        try:
            if path == "/health":
                return self._send(200, {"ok": True, "data": str(DATA), "profile_llm": bool(LLM_KEY)})
            if path == "/context":
                side = q.get("for", "story")
                if side not in SOURCES:
                    return self._send(400, {"error": "for 必须是 story 或 robot"})
                return self._send(200, build_context(side, uid, q.get("peek") == "1"))
            if path == "/all":
                with LOCK:
                    return self._send(200, {"events": load_events(uid),
                                            "profile": load_json(uid, "profile.json", empty_profile(uid)),
                                            "cursors": load_json(uid, "cursors.json", {"story": 0, "robot": 0})})
            self._send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": str(e)})

    def do_POST(self):
        path, q, uid = self._query()
        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            data = json.loads(raw or b"{}")
            if path == "/events":
                uid = safe_user(data.get("user_id") or uid)
                ev = add_event(data, uid)
                return self._send(200, {"ok": True, "id": ev["id"]})
            if path == "/profile/refresh":
                if q.get("wait") == "1":
                    return self._send(200, refresh_profile(uid))
                threading.Thread(target=lambda: _safe_refresh(uid), daemon=True).start()
                return self._send(200, {"ok": True, "started": True})
            if path == "/reset":
                reset_user(uid)
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": str(e)})

    def log_message(self, *args):
        pass


def _safe_refresh(uid: str) -> None:
    try:
        refresh_profile(uid)
    except Exception as e:  # noqa: BLE001
        print(f"[记忆] 画像整理失败：{e}", flush=True)


if __name__ == "__main__":
    DATA.mkdir(parents=True, exist_ok=True)
    print(f"记忆本已启动：http://127.0.0.1:{PORT}  数据目录：{DATA}  画像整理：{'开' if LLM_KEY else '关（没有 DASHSCOPE_API_KEY）'}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
