"""端到端自测（语音模式，不开麦克风）：用 macOS 自带中文语音合成"主人说的话"，当成麦克风听到的声音喂给大脑。
检查：本地识别是否正确 → 回复 → 记忆本。前提：记忆本（8900）和大脑（8910）已启动。
用法：python3 tests/e2e_voice.py
"""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

NP = {"http": None, "https": None}
M, B = "http://127.0.0.1:8900", "http://127.0.0.1:8910"
VOICE = "Grandma (中文（中国大陆）)"   # Flo 音色太机械，识别率低，换成 Grandma
LINES = ["我回来了。", "今天被老板当着大家的面骂了，好累。", "晚安。"]


def make_wav(text: str, out: Path) -> None:
    aiff = out.with_suffix(".aiff")
    subprocess.run(["say", "-v", VOICE, text, "-o", str(aiff)], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(out)], check=True)


def hear(path: Path, timeout=60):
    n0 = len(requests.get(f"{B}/state", proxies=NP).json()["transcript"])
    requests.post(f"{B}/hear_file", json={"path": str(path)}, proxies=NP)
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = requests.get(f"{B}/state", proxies=NP).json()
        if len(s["transcript"]) >= n0 + 2 and s["status"] in ("等待", "在听"):
            return s["transcript"][-2], s["transcript"][-1]
        time.sleep(0.3)
    raise TimeoutError(path)


def main():
    requests.post(f"{B}/reset", proxies=NP)
    requests.post(f"{M}/events", proxies=NP, json={"source": "story", "type": "choice", "importance": 2,
                                                   "summary": "在故事里，主人答应了周末陪小屿去看海。"})
    ok = 0
    with tempfile.TemporaryDirectory() as d:
        for i, line in enumerate(LINES):
            wav = Path(d) / f"u{i}.wav"
            make_wav(line, wav)
            user, bot = hear(wav)
            same = user["text"].strip("。") == line.strip("。")
            ok += same
            print(f"说：{line}\n  识别：{user['text']}（{'对' if same else '不一致'}，语气 {user.get('tone')}，{user.get('asr_ms')}ms）"
                  f"\n  小屿：{bot['text']}（{bot.get('emotion')} / {bot.get('expression')}，想 {bot.get('llm_ms')}ms，合成 {bot.get('tts_ms')}ms）")
    ev = requests.get(f"{M}/all", proxies=NP).json()["events"]
    print("记忆本：", " / ".join(f"[{e['source']}] {e['summary']}" for e in ev))
    print(f"识别正确 {ok}/{len(LINES)}")
    return 0 if ok == len(LINES) else 1


if __name__ == "__main__":
    sys.exit(main())
