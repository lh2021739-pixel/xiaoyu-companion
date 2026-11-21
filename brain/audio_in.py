"""收音 + 简单的"说完一句"判断（按音量）。机器人说话时 muted=True，不收音。

收音来源两种（MIC_SOURCE）：
- mac：Mac 麦克风（sounddevice）
- head：机器人头自己的麦克风。用 adb 在头部持续运行 tinycap，把声音流式传回 Mac。
  头部声卡只支持 44100Hz，双声道里左声道是麦克风，右声道是喇叭回采，只用左声道。
"""
from __future__ import annotations

import queue
import subprocess
import threading
import time
from collections import deque
from typing import Callable

import numpy as np

RATE = 16000
FRAME = 480                 # 30ms
END_SILENCE = 0.9           # 安静 0.9 秒算说完
MIN_SPEECH = 0.4
MAX_SPEECH = 15.0
START_DB_ABOVE_NOISE = 12.0


def _db(x: np.ndarray) -> float:
    return 20 * np.log10(np.sqrt(np.mean(x * x)) + 1e-9)


HEAD_APP = "org.zksj.robot"   # 机器人自带的对话应用：运行时会占住声卡、抢着回答，必须停掉
HEAD_RATE = 44100
UNMUTE_GUARD = 0.6            # 机器人说完后再丢弃 0.6 秒：头部收音经网络传回有延迟，防止听到自己的尾音


class HeadMicSource:
    """从机器人头持续收音，按 30ms 一帧（16kHz）放进队列。断了自动重连。"""

    def __init__(self, serial: str, q: queue.Queue, on_note: Callable[[str], None] | None = None):
        self.serial = serial
        self.q = q
        self.note = on_note or (lambda _m: None)
        self.error = ""
        self.connected = False

    def _adb(self, *args: str, timeout: float = 10) -> str:
        """只返回正常输出；adb 报错（比如头部离线）时返回空，避免把报错文字误当成结果。"""
        r = subprocess.run(["adb", "-s", self.serial, *args], capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") if r.returncode == 0 else ""

    def prepare(self) -> None:
        """连上头部、停掉自带应用、打开麦克风通道。"""
        subprocess.run(["adb", "connect", self.serial], capture_output=True, timeout=10)
        if self._adb("shell", f"pidof {HEAD_APP}").strip():
            self._adb("shell", f"am force-stop {HEAD_APP}")
            self.note("已停掉机器人自带的对话应用（它会占住声卡、抢着回答）")
        self._adb("shell", "tinymix -D 0 'Capture MIC Path' 'Hands Free Mic'")

    def start(self) -> None:
        try:
            self.prepare()
        except Exception as e:  # noqa: BLE001
            self.error = f"连不上机器人头 {self.serial}：{e}"
            return
        threading.Thread(target=self._loop, daemon=True).start()
        threading.Thread(target=self._watch_app, daemon=True).start()

    def _watch_app(self) -> None:
        """自带应用被人重新打开（比如点了头部画面）时，再停掉它。"""
        while True:
            time.sleep(15)
            try:
                if self._adb("shell", f"pidof {HEAD_APP}", timeout=5).strip():
                    self._adb("shell", f"am force-stop {HEAD_APP}", timeout=5)
                    self._adb("shell", "tinymix -D 0 'Capture MIC Path' 'Hands Free Mic'", timeout=5)
                    self.note("机器人自带的对话应用又被打开了，已重新停掉")
            except Exception:  # noqa: BLE001
                pass

    def _loop(self) -> None:
        from scipy.signal import resample_poly
        block = 4410 * 4                     # 0.1 秒（44100Hz、双声道、16 位）
        while True:
            proc = None
            try:
                proc = subprocess.Popen(
                    ["adb", "-s", self.serial, "exec-out",
                     "tinymix -D 0 'Capture MIC Path' 'Hands Free Mic' >/dev/null; "
                     f"tinycap /dev/stdout -D 0 -d 0 -c 2 -r {HEAD_RATE} -b 16 -T 86400 2>/dev/null"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
                head = b""
                while b"\n" not in head:      # 跳过开头那行 "Capturing sample: ..."
                    c = proc.stdout.read(1)
                    if not c:
                        raise RuntimeError("头部收音没有数据")
                    head += c
                self.connected, self.error = True, ""
                self.note("机器人麦克风已开始收音")
                buf, out, dc = b"", np.zeros(0, dtype=np.float32), 0.0
                while True:
                    chunk = proc.stdout.read(block - len(buf))
                    if not chunk:
                        raise RuntimeError("头部收音中断")
                    buf += chunk
                    if len(buf) < block:
                        continue
                    a = np.frombuffer(buf, dtype="<i2").reshape(-1, 2)[:, 0].astype(np.float32) / 32768.0
                    buf = b""
                    dc = 0.995 * dc + 0.005 * float(a.mean())      # 去掉直流偏置
                    out = np.concatenate([out, resample_poly(a - dc, 160, 441).astype(np.float32)])
                    while len(out) >= FRAME:
                        self.q.put(out[:FRAME].copy())
                        out = out[FRAME:]
            except Exception as e:  # noqa: BLE001
                self.connected = False
                self.error = f"机器人麦克风断了：{e}，2 秒后重连"
                self.note(self.error)
            finally:
                if proc:
                    proc.kill()
            time.sleep(2)
            try:
                self.prepare()
            except Exception:  # noqa: BLE001
                pass


class MicListener:
    def __init__(self, on_utterance: Callable[[np.ndarray], None], on_level: Callable[[float, bool], None] | None = None,
                 source: str = "mac", head_serial: str = "", on_note: Callable[[str], None] | None = None):
        self.on_utterance = on_utterance
        self.on_level = on_level
        self.muted = threading.Event()
        self.q: queue.Queue = queue.Queue()
        self.stream = None
        self.noise_db = -50.0
        self.error = ""
        self.source = source
        self.head_serial = head_serial
        self.on_note = on_note
        self.head: HeadMicSource | None = None
        self.guard_until = 0.0

    def unmute(self) -> None:
        """机器人说完后恢复收音。头部收音有传输延迟，再多丢一小段，防止听到自己的尾音。"""
        if self.source == "head":
            self.guard_until = time.time() + UNMUTE_GUARD
        self.muted.clear()

    def label(self) -> str:
        return "机器人麦克风" if self.source == "head" else "Mac 麦克风"

    def start(self) -> None:
        if self.source == "head":
            if not self.head_serial:
                self.error = "MIC_SOURCE=head 需要设置 HEAD_ADB_SERIAL（头部IP:5555）"
                return
            self.head = HeadMicSource(self.head_serial, self.q, self.on_note)
            self.head.start()
            if self.head.error:
                self.error = self.head.error
                return
            threading.Thread(target=self._loop, daemon=True).start()
            return
        import sounddevice as sd
        try:
            self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=FRAME,
                                         callback=lambda data, *_: self.q.put(data[:, 0].copy()))
            self.stream.start()
        except Exception as e:  # noqa: BLE001
            self.error = f"麦克风打不开：{e}"
            return
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        pre = deque(maxlen=10)          # 说话前 0.3 秒，防止吞掉第一个字
        speech: list[np.ndarray] = []
        voiced, silent = 0, 0.0
        in_speech = False
        while True:
            frame = self.q.get()
            if self.muted.is_set() or time.time() < self.guard_until:
                pre.clear(); speech = []; in_speech = False; voiced = 0; silent = 0.0
                continue
            level = _db(frame)
            if not in_speech:
                self.noise_db = 0.98 * self.noise_db + 0.02 * level if level < self.noise_db + 6 else self.noise_db
            loud = level > self.noise_db + START_DB_ABOVE_NOISE
            if self.on_level:
                self.on_level(level, in_speech)
            if not in_speech:
                pre.append(frame)
                voiced = voiced + 1 if loud else 0
                if voiced >= 3:
                    in_speech, speech, silent = True, list(pre), 0.0
                continue
            speech.append(frame)
            silent = 0.0 if loud else silent + FRAME / RATE
            dur = len(speech) * FRAME / RATE
            if silent >= END_SILENCE or dur >= MAX_SPEECH:
                in_speech, voiced = False, 0
                audio = np.concatenate(speech)
                speech, pre = [], deque(maxlen=10)
                if dur - silent >= MIN_SPEECH:
                    threading.Thread(target=self.on_utterance, args=(audio,), daemon=True).start()
                time.sleep(0)
