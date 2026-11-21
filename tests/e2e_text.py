"""端到端自测（打字模式，不需要麦克风）：
导入角色 → 以角色身份带着记忆聊天 → 聊天写回记忆库 → 换角色（人设和记忆隔离）→ 换回来（记忆还在）。
前提：记忆库（8900）和大脑（8910）已启动。用法：python3 tests/e2e_text.py
注意：会清空记忆库里 demo 用户的全部数据，再导入两个测试角色。
"""
import json
import sys
import time
from pathlib import Path

import requests

NP = {"http": None, "https": None, "all": None}
M, B = "http://127.0.0.1:8900", "http://127.0.0.1:8910"
SAMPLES = Path(__file__).resolve().parent.parent / "tools" / "sample_characters"
WIFE_DETAILS = ("图书馆", "小王子", "热可可", "江边", "向日葵", "番茄牛腩", "洱海", "吃饭", "画", "求婚")


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


def load(name, activate=True):
    data = json.load(open(SAMPLES / name, encoding="utf-8"))
    data["activate"] = activate
    return requests.post(f"{M}/characters/import", json=data, proxies=NP).json()


def ctx_text():
    return requests.get(f"{M}/context", params={"for": "robot"}, proxies=NP).json()["memory_text"]


def main():
    r = []
    requests.post(f"{M}/reset", params={"scope": "all"}, proxies=NP)
    load("friend_test.json", activate=False)
    res = load("wife_test.json")
    r.append(check("导入亡妻并设为当前角色", res.get("ok") and res.get("active"), json.dumps(res, ensure_ascii=False)))
    requests.post(f"{B}/reset", proxies=NP)

    a = say("我回来了。")
    r.append(check("以亡妻身份开口，并提起一件共同经历", a["role"] == "苏念" and any(w in a["text"] for w in WIFE_DETAILS),
                   f"{a['role']}：{a['text']}"))
    a = say("我好想你，要是你还在就好了。", "难过")
    r.append(check("想念时判断为想念或难过，并做难过表情", a.get("emotion") in ("想念", "难过") and a.get("expression") == "sad",
                   f"{a.get('emotion')} / {a.get('expression')} / {a['text']}"))
    a = say("你还记得你最后跟我说的话吗？")
    r.append(check("记得离开时的经历", any(w in a["text"] for w in ("好好", "要好好的")), a["text"]))
    a = say("今天在公司被老板当众批评了，好累。", "难过")
    r.append(check("接住难过的情绪", a.get("expression") == "sad", f"{a.get('emotion')} / {a['text']}"))
    a = say("嗯，晚安。")
    s = requests.get(f"{B}/state", proxies=NP).json()
    r.append(check("道别后结束这次相处", not s["session"]["active"], a["text"]))
    time.sleep(10)
    mem = requests.get(f"{M}/all", proxies=NP).json()
    robot_ev = [e["summary"] for e in mem["events"] if e["source"] == "robot"]
    r.append(check("聊天写回了亡妻的记忆", any("老板" in x or "批评" in x for x in robot_ev), " / ".join(robot_ev)))
    r.append(check("长期记忆整理出了关于他的事", any("老板" in f.get("text", "") or "批评" in f.get("text", "")
                                                   for f in mem["profile"].get("facts", [])),
                   json.dumps(mem["profile"].get("facts"), ensure_ascii=False)))

    tr = requests.get(f"{B}/state", proxies=NP).json()["transcript"]
    llm = [m["llm_ms"] for m in tr if m.get("llm_ms")]
    tts = [m["tts_ms"] for m in tr if m.get("tts_ms")]
    # 换角色：人设和记忆都换
    chars = requests.get(f"{B}/characters", proxies=NP).json()["characters"]
    friend = next(c["id"] for c in chars if c["name"] == "老周")
    requests.post(f"{B}/character/activate", json={"character_id": friend}, proxies=NP)
    a = say("在干嘛呢？")
    r.append(check("切换后以另一个角色说话", a["role"] == "老周" and "苏念" not in a["text"], f"{a['role']}：{a['text']}"))
    r.append(check("另一个角色看不到亡妻的记忆", "老板" not in ctx_text() and "向日葵" not in ctx_text()))
    say("拜拜。")
    # 换回来：记忆还在
    requests.post(f"{B}/character/activate", json={"character_id": "wife_test"}, proxies=NP)
    r.append(check("换回亡妻后，之前聊的还在", "老板" in ctx_text() or "批评" in ctx_text()))

    print(f"速度：想 平均 {sum(llm) // max(1, len(llm))}ms，语音合成 平均 {sum(tts) // max(1, len(tts))}ms（{tts}）")
    print(f"合计：{sum(r)}/{len(r)} 通过")
    return 0 if all(r) else 1


if __name__ == "__main__":
    sys.exit(main())
