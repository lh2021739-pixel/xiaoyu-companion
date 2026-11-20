"""把小屿的话和表情送出去：接了机器人头就用头部喇叭 + 表情；没接就用 Mac 喇叭，表情只在控制台显示。"""
from __future__ import annotations

import base64
import io
import subprocess
import tempfile
import threading
import wave

import numpy as np
import requests

from config import HEAD_URL, MUTE

NO_PROXY = {"http": None, "https": None}


def wav_info(wav: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(wav)) as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16 if width == 2 else np.uint8)
    if ch > 1:
        pcm = pcm.reshape(-1, ch).mean(axis=1).astype(np.int16)
    return pcm, rate


def to_pcm16k(wav: bytes) -> bytes:
    pcm, rate = wav_info(wav)
    if rate != 16000:
        n = int(len(pcm) * 16000 / rate)
        pcm = np.interp(np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm.astype(np.float32)).astype(np.int16)
    return pcm.tobytes()


def duration(wav: bytes) -> float:
    pcm, rate = wav_info(wav)
    return len(pcm) / rate


class Speaker:
    def __init__(self) -> None:
        self.head_ok = False      # 机器人头控制程序连得上（空跑也算）
        self.head_audio = False   # 能用头部喇叭（真机 adb 连接）
        self.head_note = "没有配置机器人头（HEAD_URL 为空）"
        self.lock = threading.Lock()
        self.check_head()

    def check_head(self) -> None:
        if not HEAD_URL:
            return
        try:
            h = requests.get(f"{HEAD_URL}/health", timeout=3, proxies=NO_PROXY).json()
            self.head_ok = True
            self.head_audio = h.get("transport") == "adb" and not h.get("dry_run")
            self.head_note = "机器人头已连接（真机）" if self.head_audio else "机器人头控制程序在线，但是空跑模式，声音走 Mac 喇叭"
        except Exception as e:  # noqa: BLE001
            self.head_ok = False
            self.head_note = f"连不上机器人头：{e.__class__.__name__}"

    def expression(self, name: str) -> str:
        if not self.head_ok:
            return "只在控制台显示"
        try:
            r = requests.post(f"{HEAD_URL}/action", json={"action": name}, timeout=3, proxies=NO_PROXY)
            return "已发给机器人头" if r.ok else f"机器人头拒绝：{r.text[:60]}"
        except Exception as e:  # noqa: BLE001
            return f"发送失败：{e.__class__.__name__}"

    def say(self, wav: bytes) -> str:
        """阻塞到说完。返回走了哪条路。"""
        with self.lock:
            if MUTE:
                return "静音模式（测试用，没有出声）"
            if self.head_audio:
                try:
                    r = requests.post(f"{HEAD_URL}/audio/play", timeout=60, proxies=NO_PROXY,
                                      json={"audio": base64.b64encode(to_pcm16k(wav)).decode(), "sample_rate": 16000,
                                            "channels": 1, "bytes_per_sample": 2, "drive_mouth": True, "wait": True})
                    if r.ok:
                        return "机器人头喇叭"
                except Exception:  # noqa: BLE001
                    pass
            with tempfile.NamedTemporaryFile(suffix=".wav") as f:
                f.write(wav)
                f.flush()
                subprocess.run(["afplay", f.name], check=False)
            return "Mac 喇叭"
