"""对话：人设 + 情绪规则 + 记忆 → 一轮回复（JSON）。人设和规则是纯文字文件，情感同学改文字即可。"""
from __future__ import annotations

import json
import time

import requests

from config import CHAT_KEY, CHAT_MODEL, CHAT_URL, PROMPTS

EXPRESSIONS = ["smile", "sad", "makeface", "kiss", "blink", "init"]
EMOTIONS = ["开心", "难过", "生气", "累", "平静"]

OUTPUT_RULES = """
每一轮只输出一个 JSON，不要任何其他文字：
{"emotion":"开心|难过|生气|累|平静（主人现在的情绪）",
 "expression":"smile|sad|makeface|kiss|blink|init（你要做的表情）",
 "reply":"你要说的话（口语、简短、不用表情符号）",
 "remember":[{"type":"said 或 emotion","summary":"一句第三人称的话","importance":1到3}],
 "end_session":false}
- remember 没有就给空数组。约定、重要的事 importance 填 2 或 3。
- 主人说晚安、拜拜、要走了，end_session 填 true，并好好告别。
"""


def build_system(memory_text: str) -> str:
    persona = (PROMPTS / "persona.md").read_text(encoding="utf-8")
    rules = (PROMPTS / "emotion_rules.md").read_text(encoding="utf-8")
    mem = memory_text.strip() or "（暂时没有记忆）"
    return f"{persona}\n\n{rules}\n\n【记忆】\n{mem}\n\n{OUTPUT_RULES}"


def _clean(obj: dict) -> dict:
    reply = str(obj.get("reply") or "").strip() or "嗯，我在听。"
    emotion = obj.get("emotion") if obj.get("emotion") in EMOTIONS else "平静"
    expression = obj.get("expression") if obj.get("expression") in EXPRESSIONS else "init"
    remember = []
    for r in obj.get("remember") or []:
        if isinstance(r, dict) and str(r.get("summary") or "").strip():
            remember.append({"type": "emotion" if r.get("type") == "emotion" else "said",
                             "summary": str(r["summary"]).strip(),
                             "importance": max(1, min(3, int(r.get("importance") or 1)))})
    return {"emotion": emotion, "expression": expression, "reply": reply,
            "remember": remember, "end_session": bool(obj.get("end_session"))}


def chat_turn(memory_text: str, history: list[dict], user_text: str, tone_cn: str, hint: str = "") -> dict:
    """history：[{role:user|assistant, content}]，只放本次相处最近几轮。"""
    messages = [{"role": "system", "content": build_system(memory_text)}, *history[-12:],
                {"role": "user", "content": f"主人说：{user_text}\n（语气标签：{tone_cn}）{hint}"}]
    t0 = time.time()
    r = requests.post(CHAT_URL, timeout=30, headers={"Authorization": f"Bearer {CHAT_KEY}"},
                      json={"model": CHAT_MODEL, "messages": messages, "temperature": 0.7,
                            "response_format": {"type": "json_object"}, "max_tokens": 400})
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {"reply": content}
    if isinstance(parsed, list):   # 偶尔会给一个数组，取第一个对象
        parsed = next((x for x in parsed if isinstance(x, dict)), {})
    out = _clean(parsed if isinstance(parsed, dict) else {})
    out["ms"] = int((time.time() - t0) * 1000)
    return out


def opening_line(memory_text: str) -> dict:
    """主人说"我回来了"开启一次相处时用：同 chat_turn，只是提示它主动接住记忆。"""
    return chat_turn(memory_text, [], "我回来了。", "平静")
