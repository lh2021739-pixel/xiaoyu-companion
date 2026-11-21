"""记忆库：多角色。故事平台一键导出角色（人设 + 经历）→ 导入这里并标记为"当前角色"；
机器人读取当前角色的人设和记忆说话，聊天内容也存回这个角色名下。换一个角色导入，机器人就换成另一个人。

启动：python3 memory/memory_server.py   （只用 Python 自带库；端口 8900）
数据：memory_data/<user_id>/chars/<character_id>/（card.json、events.jsonl、profile.json），
      memory_data/<user_id>/active.json 记录当前角色。
整理长期记忆需要环境变量 DASHSCOPE_API_KEY；没有也能跑，只是不整理。

接口（详见 docs/记忆库接口.md）：
  POST /characters/import       导入一个角色（人设 + 经历），默认设为当前角色
  POST /characters/activate     切换当前角色 {"character_id": "..."}
  GET  /characters              角色列表（标出当前角色）
  GET  /context?for=robot       当前角色的人设 + 记忆，已拼成给机器人的文字
  POST /events                  写一条事件（机器人聊天时写 source=robot）
  POST /profile/refresh         把新事件整理进长期记忆（一次相处结束时调用）
  GET  /all                     当前角色的全部内容（演示大屏用）
  POST /reset?scope=chat|all    chat：清空当前角色的聊天记忆（保留导入的经历）；all：清空全部
  GET  /health
所有接口都可带 ?user_id=xxx（默认 demo），以及 ?character_id=xxx（默认当前角色）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
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
TYPES = {"story_event", "choice", "said", "emotion"}
CARD_FIELDS = ("name", "call_user", "personality", "speaking_style", "background", "relationship", "ending", "guidance")
LOCK = threading.RLock()


def now_iso() -> str:
    return datetime.now(CN).isoformat(timespec="seconds")


def safe_id(x: str, default: str = "demo") -> str:
    x = (x or "").strip()
    return x if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", x) else default


# ── 存储 ───────────────────────────────────────────────────────────────
def user_dir(uid: str) -> Path:
    d = DATA / safe_id(uid)
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    except json.JSONDecodeError:
        return default


def write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def active_id(uid: str) -> str | None:
    return read_json(user_dir(uid) / "active.json", {}).get("character_id")


def char_dir(uid: str, cid: str | None) -> Path | None:
    cid = cid or active_id(uid)
    if not cid:
        return None
    return user_dir(uid) / "chars" / safe_id(cid, "")


def load_events(cdir: Path) -> list[dict]:
    p = cdir / "events.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def empty_profile() -> dict:
    return {"facts": [], "likes": [], "promises": [], "relationship_now": "", "merged_upto": 0, "updated_at": None}


# ── 角色导入 / 切换 ────────────────────────────────────────────────────
def import_character(data: dict, uid: str) -> dict:
    card_in = data.get("character") or {}
    name = str(card_in.get("name") or "").strip()
    if not name:
        raise ValueError("character.name 不能为空")
    cid = safe_id(str(card_in.get("id") or ""), "") or f"char_{hashlib.md5(name.encode()).hexdigest()[:8]}"
    card = {"id": cid, **{k: str(card_in.get(k) or "").strip() for k in CARD_FIELDS},
            "voice": card_in.get("voice"), "imported_at": now_iso(), "source": data.get("source") or "story_platform"}
    with LOCK:
        cdir = user_dir(uid) / "chars" / cid
        if cdir.exists() and data.get("replace", True):
            shutil.rmtree(cdir)
        cdir.mkdir(parents=True, exist_ok=True)
        write_json(cdir / "card.json", card)
        with (cdir / "events.jsonl").open("a", encoding="utf-8") as f:
            for i, e in enumerate(data.get("events") or []):
                summary = str(e.get("summary") or "").strip() if isinstance(e, dict) else str(e).strip()
                if not summary:
                    continue
                f.write(json.dumps({
                    "id": f"story_{i + 1}", "time": now_iso(), "story_time": (e.get("story_time") if isinstance(e, dict) else None),
                    "source": "story", "type": "story_event", "summary": summary[:400],
                    "importance": int((e.get("importance") if isinstance(e, dict) else 0) or 2),
                }, ensure_ascii=False) + "\n")
        write_json(cdir / "profile.json", empty_profile())
        if data.get("activate", True):
            write_json(user_dir(uid) / "active.json", {"character_id": cid, "since": now_iso()})
    n = len(load_events(cdir))
    print(f"[记忆库] 导入角色 {name}（{cid}），经历 {n} 条{'，已设为当前角色' if data.get('activate', True) else ''}", flush=True)
    return {"ok": True, "character_id": cid, "events": n, "active": data.get("activate", True)}


def list_characters(uid: str) -> list[dict]:
    base = user_dir(uid) / "chars"
    act = active_id(uid)
    out = []
    for d in sorted(base.iterdir()) if base.exists() else []:
        card = read_json(d / "card.json", None)
        if card:
            out.append({"id": card["id"], "name": card.get("name"), "imported_at": card.get("imported_at"),
                        "active": card["id"] == act, "events": len(load_events(d))})
    return out


# ── 写事件 ─────────────────────────────────────────────────────────────
def add_event(data: dict, uid: str, cid: str | None) -> dict:
    cdir = char_dir(uid, cid)
    if not cdir or not (cdir / "card.json").exists():
        raise ValueError("还没有导入角色，或 character_id 不存在")
    source, etype = data.get("source"), data.get("type")
    summary = str(data.get("summary") or "").strip()
    if source not in SOURCES:
        raise ValueError("source 必须是 story 或 robot")
    if etype not in TYPES:
        raise ValueError("type 必须是 story_event / choice / said / emotion")
    if not summary:
        raise ValueError("summary 不能为空")
    norm = lambda x: re.sub(r"[，。！？、,.!?\s特别非常很]", "", x)
    with LOCK:
        events = load_events(cdir)
        for old in events[-20:]:
            if old["source"] == source and old["type"] == etype and \
                    SequenceMatcher(None, norm(old["summary"]), norm(summary)).ratio() > 0.85:
                return {**old, "duplicate": True}
        event = {"id": f"evt_{datetime.now(CN).strftime('%Y%m%d_%H%M%S')}_{len(events) + 1}", "time": now_iso(),
                 "source": source, "type": etype, "summary": summary[:300], "detail": data.get("detail"),
                 "emotion": data.get("emotion"), "importance": int(data.get("importance") or (3 if etype == "emotion" else 1))}
        with (cdir / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    print(f"[记忆库] +{source}/{etype}: {summary}", flush=True)
    return event


# ── 给机器人的上下文 ───────────────────────────────────────────────────
def _emo(e: dict) -> str:
    emo = e.get("emotion") or {}
    return f"（情绪：{emo['label']}）" if isinstance(emo, dict) and emo.get("label") else ""


def persona_text(card: dict) -> str:
    rows = [f"你是「{card['name']}」。"]
    labels = {"background": "你的身份和经历", "personality": "你的性格", "speaking_style": "你说话的方式",
              "call_user": "你对他的称呼", "relationship": "你们的关系", "ending": "你们故事的结局",
              "guidance": "你现在和他相处的方式"}
    for k, label in labels.items():
        if card.get(k):
            rows.append(f"- {label}：{card[k]}")
    return "\n".join(rows)


def build_robot_context(uid: str, cid: str | None) -> dict:
    cdir = char_dir(uid, cid)
    if not cdir or not (cdir / "card.json").exists():
        return {"has_character": False, "character": None, "persona_text": "", "memory_text": ""}
    with LOCK:
        card = read_json(cdir / "card.json", {})
        events = load_events(cdir)
        prof = read_json(cdir / "profile.json", empty_profile())
    story = [e for e in events if e["source"] == "story"]
    story = sorted(story, key=lambda e: -e.get("importance", 1))[:40]
    story.sort(key=lambda e: e["id"])
    chats = [e for e in events if e["source"] == "robot" and e["type"] == "said"][-15:]
    emotions = [e for e in events if e["type"] == "emotion"][-3:]
    lines = []
    if story:
        lines.append("【你们一起经历过的事（来自故事，按时间）】")
        lines += [f"- {e['summary']}" for e in story]
    if chats:
        lines.append("【后来你们又聊过的】")
        lines += [f"- {e['summary']}" for e in chats]
    keep = [f"- 约定：{p['text']}（{p.get('status') or '未兑现'}）" for p in prof.get("promises", []) if p.get("status") != "已兑现"]
    keep += [f"- 他的喜好：{x['text']}" for x in prof.get("likes", [])[-5:]]
    keep += [f"- 关于他：{x['text']}" for x in prof.get("facts", [])[-6:]]
    if prof.get("relationship_now"):
        keep.append(f"- 你们现在：{prof['relationship_now']}")
    if keep:
        lines.append("【这段时间你记住的】")
        lines += keep
    if emotions:
        lines.append("【他最近的情绪】")
        lines += [f"- {e['summary']}{_emo(e)}" for e in emotions]
    return {"has_character": True, "character": card, "persona_text": persona_text(card),
            "memory_text": "\n".join(lines), "text": persona_text(card) + "\n\n" + "\n".join(lines)}


# ── 整理长期记忆 ───────────────────────────────────────────────────────
PROFILE_PROMPT = """你在维护角色「{name}」对他（用户）的长期记忆。根据【新事件】更新【当前记忆】，只输出完整 JSON，不要解释。
规则：只写事件里明确出现的信息，不编造；每条一句话，去重，保留旧的。
- facts：关于他的事实（工作、处境、身边的人、最近发生的事）
- likes：他的喜好
- promises：他和{name}之间的约定，status 为"未兑现"或"已兑现"
- relationship_now：一句话，描述两人现在的状态
结构：{{"facts":[{{"text":""}}],"likes":[{{"text":""}}],"promises":[{{"text":"","status":"未兑现"}}],"relationship_now":""}}

【当前记忆】
{profile}

【新事件】
{events}"""


def refresh_profile(uid: str, cid: str | None) -> dict:
    cdir = char_dir(uid, cid)
    if not cdir or not (cdir / "card.json").exists():
        return {"ok": False, "error": "没有当前角色"}
    with LOCK:
        card = read_json(cdir / "card.json", {})
        prof = read_json(cdir / "profile.json", empty_profile())
        events = load_events(cdir)
        start = int(prof.get("merged_upto", 0))
        new = [e for e in events[start:] if e["source"] == "robot"]
        upto = len(events)
    if not new:
        with LOCK:
            prof["merged_upto"] = upto
            write_json(cdir / "profile.json", prof)
        return {"ok": True, "merged": 0, "profile": prof}
    if not LLM_KEY:
        return {"ok": False, "merged": 0, "error": "没有 DASHSCOPE_API_KEY，跳过整理"}
    shown = {k: prof.get(k) for k in ("facts", "likes", "promises", "relationship_now")}
    prompt = PROFILE_PROMPT.format(name=card.get("name", "角色"), profile=json.dumps(shown, ensure_ascii=False),
                                   events="\n".join(f"- {e['summary']}{_emo(e)}" for e in new))
    body = {"model": LLM_MODEL, "temperature": 0.2, "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(LLM_URL, json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": f"Bearer {LLM_KEY}"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=60) as r:
        updated = json.loads(json.loads(r.read())["choices"][0]["message"]["content"])
    with LOCK:
        prof = read_json(cdir / "profile.json", empty_profile())
        for k in ("facts", "likes", "promises", "relationship_now"):
            if k in updated:
                prof[k] = updated[k]
        prof["merged_upto"] = upto
        prof["updated_at"] = now_iso()
        write_json(cdir / "profile.json", prof)
    print(f"[记忆库] 已整理 {len(new)} 条聊天记忆（{time.time() - t0:.1f}s）", flush=True)
    return {"ok": True, "merged": len(new), "profile": prof}


def reset(uid: str, scope: str) -> None:
    with LOCK:
        if scope == "all":
            shutil.rmtree(user_dir(uid))
            return
        cdir = char_dir(uid, None)
        if not cdir or not cdir.exists():
            return
        keep = [e for e in load_events(cdir) if e["source"] == "story"]
        (cdir / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in keep), encoding="utf-8")
        write_json(cdir / "profile.json", empty_profile())
    print(f"[记忆库] 已清空（{scope}）", flush=True)


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

    def _q(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        return url.path, q, safe_id(q.get("user_id", "demo")), (q.get("character_id") or None)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        path, q, uid, cid = self._q()
        try:
            if path == "/health":
                return self._send(200, {"ok": True, "data": str(DATA), "profile_llm": bool(LLM_KEY), "active": active_id(uid)})
            if path == "/characters":
                return self._send(200, {"characters": list_characters(uid), "active": active_id(uid)})
            if path == "/context":
                return self._send(200, build_robot_context(uid, cid))
            if path == "/all":
                cdir = char_dir(uid, cid)
                if not cdir or not (cdir / "card.json").exists():
                    return self._send(200, {"character": None, "events": [], "profile": empty_profile(),
                                            "characters": list_characters(uid)})
                with LOCK:
                    return self._send(200, {"character": read_json(cdir / "card.json", {}), "events": load_events(cdir),
                                            "profile": read_json(cdir / "profile.json", empty_profile()),
                                            "characters": list_characters(uid)})
            self._send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": str(e)})

    def do_POST(self):
        path, q, uid, cid = self._q()
        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            data = json.loads(raw or b"{}")
            uid = safe_id(data.get("user_id") or uid)
            if path == "/characters/import":
                return self._send(200, import_character(data, uid))
            if path == "/characters/activate":
                target = safe_id(str(data.get("character_id") or ""), "")
                if not (user_dir(uid) / "chars" / target / "card.json").exists():
                    return self._send(400, {"error": "没有这个角色"})
                write_json(user_dir(uid) / "active.json", {"character_id": target, "since": now_iso()})
                print(f"[记忆库] 当前角色切换为 {target}", flush=True)
                return self._send(200, {"ok": True, "active": target})
            if path == "/events":
                ev = add_event(data, uid, data.get("character_id") or cid)
                return self._send(200, {"ok": True, "id": ev["id"], "duplicate": ev.get("duplicate", False)})
            if path == "/profile/refresh":
                if q.get("wait") == "1":
                    return self._send(200, refresh_profile(uid, cid))
                threading.Thread(target=_safe_refresh, args=(uid, cid), daemon=True).start()
                return self._send(200, {"ok": True, "started": True})
            if path == "/reset":
                reset(uid, q.get("scope", "chat"))
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": str(e)})

    def log_message(self, *args):
        pass


def _safe_refresh(uid: str, cid: str | None) -> None:
    try:
        refresh_profile(uid, cid)
    except Exception as e:  # noqa: BLE001
        print(f"[记忆库] 整理失败：{e}", flush=True)


if __name__ == "__main__":
    DATA.mkdir(parents=True, exist_ok=True)
    print(f"记忆库已启动：http://127.0.0.1:{PORT}  数据：{DATA}  长期记忆整理：{'开' if LLM_KEY else '关'}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
