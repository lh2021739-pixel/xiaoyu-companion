"""机器人大脑的配置：全部来自环境变量（Key 不进仓库）。"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROMPTS = ROOT / "prompts"


def _load_keys_file() -> None:
    """KEYS_FILE 指向一个本地 KEY=VALUE 文件时，把里面的值补进环境变量（已存在的不覆盖）。"""
    path = os.environ.get("KEYS_FILE")
    if not path or not Path(path).is_file():
        return
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"'))


_load_keys_file()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


DASHSCOPE_KEY = env("DASHSCOPE_API_KEY")
STEPFUN_KEY = env("STEPFUN_API_KEY")
STEPFUN_BASE = env("STEPFUN_BASE_URL", "https://api.stepfun.com/step_plan/v1").rstrip("/")

# 对话模型（要快）：默认百炼 qwen-flash，OpenAI 兼容接口
CHAT_URL = env("CHAT_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
CHAT_MODEL = env("CHAT_MODEL", "qwen-flash")   # 实测 qwen-flash 约 0.7 秒、qwen-plus 约 1.5 秒，效果相当
CHAT_KEY = env("CHAT_API_KEY", DASHSCOPE_KEY)

# 语音合成：默认阶跃 stepaudio-2.5-tts，失败自动换百炼 qwen3-tts-flash
TTS_PROVIDER = env("TTS_PROVIDER", "stepfun")
TTS_VOICE = env("TTS_VOICE", "wenrounvsheng")
TTS_FALLBACK_VOICE = env("TTS_FALLBACK_VOICE", "Cherry")

# 本地语音识别
SENSEVOICE_DIR = env("SENSEVOICE_DIR", str(Path.home() / ".cache/modelscope/models/iic--SenseVoiceSmall/snapshots/master"))

# 记忆本、机器人头、控制台
MEMORY_URL = env("MEMORY_URL", "http://127.0.0.1:8900").rstrip("/")
USER_ID = env("USER_ID", "demo")
HEAD_URL = env("HEAD_URL", "").rstrip("/")          # 例如 http://127.0.0.1:8787；空 = 不接机器人头
CONSOLE_PORT = int(env("CONSOLE_PORT", "8910"))

# 开关
MIC = env("MIC", "1") == "1"                         # 0 = 不开麦克风（只用控制台打字）
MUTE = env("MUTE", "0") == "1"                       # 1 = 不出声（测试用）
ACK = env("ACK", "1") == "1"                         # 1 = 听完先应一声"嗯"，填补等待
CHARACTER = env("CHARACTER_NAME", "小屿")
END_WORDS = ("晚安", "拜拜", "再见", "我先睡了", "下次聊")
SESSION_IDLE_SECONDS = int(env("SESSION_IDLE_SECONDS", "180"))
