"""本地语音识别：SenseVoiceSmall（不联网）。顺带给出语气标签，情感模块会用到。"""
from __future__ import annotations

import re
import threading
import time

import numpy as np

from config import SENSEVOICE_DIR

TONE_CN = {"HAPPY": "开心", "SAD": "难过", "ANGRY": "生气", "NEUTRAL": "平静",
           "FEARFUL": "害怕", "DISGUSTED": "厌恶", "SURPRISED": "惊讶", "EMO_UNKNOWN": "不确定"}


class LocalASR:
    def __init__(self) -> None:
        self.model = None
        self.error = ""
        self.ready = threading.Event()

    def load(self) -> None:
        """后台加载（约 4 秒）。加载失败时 error 有原因，控制台照样能打字。"""
        try:
            from funasr import AutoModel
            self.model = AutoModel(model=SENSEVOICE_DIR, device="cpu", disable_update=True,
                                   disable_pbar=True, disable_log=True, trust_remote_code=False)
        except Exception as e:  # noqa: BLE001
            self.error = f"语音识别加载失败：{e}"
        finally:
            self.ready.set()

    def __call__(self, samples: np.ndarray) -> dict:
        """samples：16kHz 单声道 float32。返回 {text, tone, tone_cn, ms}。"""
        self.ready.wait(30)
        if self.model is None:
            raise RuntimeError(self.error or "语音识别没有加载")
        t0 = time.time()
        raw = self.model.generate(input=samples, fs=16000, language="zh", use_itn=True)[0].get("text", "")
        tags = re.findall(r"<\|([^|]+)\|>", raw)
        text = re.sub(r"<\|[^|]+\|>", "", raw).strip()
        tone = next((t for t in tags if t in TONE_CN), "NEUTRAL")
        return {"text": text, "tone": tone, "tone_cn": TONE_CN.get(tone, "平静"), "ms": int((time.time() - t0) * 1000)}
