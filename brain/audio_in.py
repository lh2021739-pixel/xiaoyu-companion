"""Mac 麦克风收音 + 简单的"说完一句"判断（按音量）。机器人说话时 muted=True，不收音。"""
from __future__ import annotations

import queue
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


class MicListener:
    def __init__(self, on_utterance: Callable[[np.ndarray], None], on_level: Callable[[float, bool], None] | None = None):
        self.on_utterance = on_utterance
        self.on_level = on_level
        self.muted = threading.Event()
        self.q: queue.Queue = queue.Queue()
        self.stream = None
        self.noise_db = -50.0
        self.error = ""

    def start(self) -> None:
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
            if self.muted.is_set():
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
