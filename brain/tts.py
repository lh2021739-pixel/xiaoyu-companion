"""语音合成：默认百炼 qwen3-tts-flash（更快），失败自动换阶跃 stepaudio-2.5-tts。返回 wav 字节。
角色资料里可以带 voice：{"provider": "dashscope" 或 "stepfun", "voice": "音色名"}，优先用它。"""
from __future__ import annotations

import time

import requests

HTTP = requests.Session()
HTTP.trust_env = False   # 国内接口直连，不读本机的代理设置（代理会让下载音频变慢，SOCKS 代理还会直接报错）

from config import DASHSCOPE_KEY, STEPFUN_BASE, STEPFUN_KEY, TTS_DASHSCOPE_VOICE, TTS_PROVIDER, TTS_STEPFUN_VOICE


def _stepfun(text: str, voice: str | None) -> bytes:
    r = HTTP.post(f"{STEPFUN_BASE}/audio/speech", timeout=(5, 12),
                      headers={"Authorization": f"Bearer {STEPFUN_KEY}"},
                      json={"model": "stepaudio-2.5-tts", "input": text, "voice": voice or TTS_STEPFUN_VOICE,
                            "response_format": "wav"})
    if r.status_code != 200 or not r.content.startswith(b"RIFF"):
        raise RuntimeError(f"阶跃语音合成失败 {r.status_code}: {r.text[:120]}")
    return r.content


def _dashscope(text: str, voice: str | None) -> bytes:
    r = HTTP.post("https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation", timeout=(5, 8),
                      headers={"Authorization": f"Bearer {DASHSCOPE_KEY}"},
                      json={"model": "qwen3-tts-flash",
                            "input": {"text": text, "voice": voice or TTS_DASHSCOPE_VOICE, "language_type": "Chinese"}})
    r.raise_for_status()
    url = r.json()["output"]["audio"]["url"]
    return HTTP.get(url, timeout=(5, 8)).content   # 卡住就让位给阶跃，不让对话干等


PROVIDERS = {"dashscope": (_dashscope, "百炼"), "stepfun": (_stepfun, "阶跃")}


def synthesize(text: str, voice_cfg: dict | None = None) -> dict:
    """返回 {wav, provider, ms}。voice_cfg 来自角色资料，可为空。
    格式：{"provider": "dashscope", "voice": "Chelsie", "backup": {"provider": "stepfun", "voice": "jingdiannvsheng"}}
    主音色失败时，先用 backup 指定的备用音色；没写 backup 就用另一家的默认音色。"""
    t0 = time.time()
    cfg = voice_cfg or {}
    first = cfg.get("provider") or TTS_PROVIDER
    backup = cfg.get("backup") if isinstance(cfg.get("backup"), dict) else {}
    order = [first] + [p for p in PROVIDERS if p != first]
    errors = []
    for name in order:
        fn, label = PROVIDERS.get(name, PROVIDERS["dashscope"])
        if name == first:
            voice = cfg.get("voice")
        elif backup.get("provider") == name:
            voice = backup.get("voice")
        else:
            voice = None
        try:
            return {"wav": fn(text, voice), "provider": label, "ms": int((time.time() - t0) * 1000)}
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
    raise RuntimeError("；".join(errors))
