#!/bin/bash
# 一条命令启动：记忆本（8900）+ 机器人大脑（8910，含演示控制台）。Ctrl+C 一起停。
# 常用开关（写在命令前面）：
#   MIC=0          不开麦克风，只用控制台打字
#   MUTE=1         不出声（测试用）
#   HEAD_URL=http://127.0.0.1:8787   接机器人头（先按 docs/运行说明.md 启动头部控制程序）
#   TTS_PROVIDER=dashscope           语音合成改用百炼（更快）
#   CHAT_MODEL=qwen-plus             对话改用 qwen-plus（更稳但慢一倍）
set -e
cd "$(dirname "$0")"
export KEYS_FILE="${KEYS_FILE:-$(cd .. && pwd)/.keys.env}"     # 本地密钥文件，不在仓库里
PY="${PYTHON:-python3}"                                         # 需要装了 numpy、sounddevice、funasr、requests 的 Python
if [ ! -f "$KEYS_FILE" ]; then echo "找不到密钥文件 $KEYS_FILE（需要 DASHSCOPE_API_KEY，可选 STEPFUN_API_KEY / STEPFUN_BASE_URL）"; exit 1; fi
set -a; . "$KEYS_FILE"; set +a

python3 memory/memory_server.py &
MEM=$!
trap 'kill $MEM 2>/dev/null' EXIT
sleep 1
echo "打开演示控制台：http://127.0.0.1:${CONSOLE_PORT:-8910}"
(sleep 3; open "http://127.0.0.1:${CONSOLE_PORT:-8910}" 2>/dev/null || true) &
cd brain && "$PY" brain.py
