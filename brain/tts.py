"""语音合成：默认阶跃 stepaudio-2.5-tts（套餐地址），失败自动换百炼 qwen3-tts-flash。返回 wav 字节。"""
from __future__ import annotations

import time

import requests

from config import DASHSCOPE_KEY, STEPFUN_BASE, STEPFUN_KEY, TTS_FALLBACK_VOICE, TTS_PROVIDER, TTS_VOICE


def _stepfun(text: str) -> bytes:
    r = requests.post(f"{STEPFUN_BASE}/audio/speech", timeout=30,
                      headers={"Authorization": f"Bearer {STEPFUN_KEY}"},
                      json={"model": "stepaudio-2.5-tts", "input": text, "voice": TTS_VOICE, "response_format": "wav"})
    if r.status_code != 200 or not r.content.startswith(b"RIFF"):
        raise RuntimeError(f"阶跃语音合成失败 {r.status_code}: {r.text[:120]}")
    return r.content


def _dashscope(text: str) -> bytes:
    r = requests.post("https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation", timeout=30,
                      headers={"Authorization": f"Bearer {DASHSCOPE_KEY}"},
                      json={"model": "qwen3-tts-flash", "input": {"text": text, "voice": TTS_FALLBACK_VOICE, "language_type": "Chinese"}})
    r.raise_for_status()
    url = r.json()["output"]["audio"]["url"]
    return requests.get(url, timeout=30).content


def synthesize(text: str) -> dict:
    """返回 {wav, provider, ms}。"""
    t0 = time.time()
    order = [_stepfun, _dashscope] if TTS_PROVIDER == "stepfun" else [_dashscope, _stepfun]
    errors = []
    for fn in order:
        try:
            wav = fn(text)
            return {"wav": wav, "provider": "阶跃" if fn is _stepfun else "百炼", "ms": int((time.time() - t0) * 1000)}
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
    raise RuntimeError("；".join(errors))
