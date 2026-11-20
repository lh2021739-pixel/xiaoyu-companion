"""端到端自测（打字模式，不需要麦克风）：故事里的选择 → 机器人提起；对机器人说的事 → 记进记忆本 → 故事侧能取到。
前提：记忆本（8900）和大脑（8910）已启动。用法：python3 tests/e2e_text.py
"""
import json
import sys
import time

import requests

NP = {"http": None, "https": None}
M, B = "http://127.0.0.1:8900", "http://127.0.0.1:8910"


def say(text, tone="平静", timeout=60):
    n0 = len(requests.get(f"{B}/state", proxies=NP).json()["transcript"])
    requests.post(f"{B}/say", json={"text": text, "tone": tone}, proxies=NP)
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = requests.get(f"{B}/state", proxies=NP).json()
        if len(s["transcript"]) >= n0 + 2 and s["status"] in ("等待", "在听"):
            return s["transcript"][-1]
        time.sleep(0.3)
    raise TimeoutError(text)


def check(name, ok, detail=""):
    print(f"{'通过' if ok else '未通过'} | {name} | {detail}")
    return ok


def main():
    results = []
    requests.post(f"{B}/reset", proxies=NP)
    # 1. 故事侧先发生一件事
    requests.post(f"{M}/events", proxies=NP, json={"source": "story", "type": "choice", "importance": 2,
                                                   "summary": "在故事里，主人答应了周末陪小屿去看海。"})
    # 2. 回家打招呼：要提起看海
    r = say("我回来了。")
    results.append(check("打招呼时提起故事里的约定", any(w in r["text"] for w in ("海", "周末")), r["text"]))
    # 3. 说心事：判断难过、做难过表情、记下来
    r = say("今天被老板当着大家的面骂了，好累。", "难过")
    results.append(check("判断为难过/累并做难过表情", r.get("emotion") in ("难过", "累") and r.get("expression") == "sad",
                         f"{r.get('emotion')} / {r.get('expression')} / {r['text']}"))
    time.sleep(1)
    ev = requests.get(f"{M}/all", proxies=NP).json()["events"]
    robot_ev = [e for e in ev if e["source"] == "robot"]
    results.append(check("被骂这件事写进记忆本", any("老板" in e["summary"] or "批评" in e["summary"] or "骂" in e["summary"] for e in robot_ev),
                         " / ".join(e["summary"] for e in robot_ev)))
    # 4. 喜好
    r = say("对了，我特别喜欢喝热可可。", "开心")
    results.append(check("喜好也能聊", bool(r["text"]), r["text"]))
    # 5. 道别：结束相处并整理长期记忆
    r = say("我先睡了，晚安。")
    s = requests.get(f"{B}/state", proxies=NP).json()
    results.append(check("道别后结束这次相处", not s["session"]["active"], r["text"]))
    time.sleep(12)
    prof = requests.get(f"{M}/all", proxies=NP).json()["profile"]
    results.append(check("长期记忆里有约定和喜好",
                         any("海" in p.get("text", "") for p in prof.get("promises", []))
                         and any("可可" in x.get("text", "") for x in prof.get("user", {}).get("likes", [])),
                         json.dumps({k: prof.get(k) for k in ("promises", "relationship")}, ensure_ascii=False)))
    # 6. 故事侧取上下文：拿到机器人那边的新事
    ctx = requests.get(f"{M}/context", params={"for": "story", "peek": "1"}, proxies=NP).json()
    results.append(check("故事侧能取到机器人这边的新事", ctx["has_new"] and ("老板" in ctx["text"] or "批评" in ctx["text"]), ctx["text"][:200]))
    # 速度
    tr = requests.get(f"{B}/state", proxies=NP).json()["transcript"]
    llm = [m["llm_ms"] for m in tr if m.get("llm_ms")]
    tts = [m["tts_ms"] for m in tr if m.get("tts_ms")]
    print(f"速度：想 平均 {sum(llm) // max(1, len(llm))}ms（{llm}），语音合成 平均 {sum(tts) // max(1, len(tts))}ms（{tts}）")
    print(f"合计：{sum(results)}/{len(results)} 通过")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
