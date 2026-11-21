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

# 语音合成：默认百炼 qwen3-tts-flash（约 1 秒，比阶跃快一倍），失败自动换阶跃
TTS_PROVIDER = env("TTS_PROVIDER", "dashscope")
TTS_DASHSCOPE_VOICE = env("TTS_DASHSCOPE_VOICE", "Cherry")
TTS_STEPFUN_VOICE = env("TTS_STEPFUN_VOICE", "wenrounvsheng")

# 本地语音识别
SENSEVOICE_DIR = env("SENSEVOICE_DIR", str(Path.home() / ".cache/modelscope/models/iic--SenseVoiceSmall/snapshots/master"))

# 记忆本、机器人头、控制台
MEMORY_URL = env("MEMORY_URL", "http://127.0.0.1:8900").rstrip("/")
USER_ID = env("USER_ID", "demo")
HEAD_URL = env("HEAD_URL", "").rstrip("/")          # 例如 http://127.0.0.1:8787；空 = 不接机器人头
CONSOLE_PORT = int(env("CONSOLE_PORT", "8910"))

# 开关
MIC = env("MIC", "1") == "1"                         # 0 = 不开麦克风（只用控制台打字）
MIC_SOURCE = env("MIC_SOURCE", "mac")                # mac = Mac 麦克风；head = 机器人头自己的麦克风
HEAD_ADB_SERIAL = env("HEAD_ADB_SERIAL", "")          # 机器人头的 adb 地址，例如 172.20.10.5:5555（MIC_SOURCE=head 时必填）
MUTE = env("MUTE", "0") == "1"                       # 1 = 不出声（测试用）
ACK = env("ACK", "1") == "1"                         # 1 = 听完先应一声"嗯"，填补等待
END_WORDS = ("晚安", "拜拜", "再见", "我先睡了", "下次聊")
# 开场提起共同经历时避开的伤心事（这些留给他主动提起时再聊）
SAD_EVENT_WORDS = ("去世", "离开", "抢救", "倒下", "病", "医院", "吵", "债", "失败", "挂", "静音", "哭", "害怕", "冷战", "分房")
# 安全保护：听到轻生相关的话，强制她劝他求助（对所有角色生效，不靠人设碰运气）
CRISIS_WORDS = ("不想活", "活不下去", "想死", "去死", "自杀", "轻生", "跟你一起走", "陪你一起走", "去找你", "随你而去",
                "结束自己", "了结自己", "活着没意思", "活着没意义")
CRISIS_HINT = ("（提示：他刚才说的话可能有轻生的念头，这比什么都重要。先用一句话接住他的难过，"
               "再明确说你不希望他这样做；然后必须请他现在就去找家人或朋友陪着，难受得厉害时找心理援助热线或去医院。"
               "不要说'我等你''来找我''我们很快会见面'，也不要只说'好好活着'就结束。）")
CRISIS_LINE = "可我不希望你这样。答应我，现在就去找家人或朋友陪着你；实在难受，就打心理援助热线，或者去医院，好吗？"
SESSION_IDLE_SECONDS = int(env("SESSION_IDLE_SECONDS", "180"))
