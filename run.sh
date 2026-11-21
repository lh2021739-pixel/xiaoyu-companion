#!/bin/bash
# 一条命令启动：记忆本（8900）+ 机器人大脑（8910，含演示控制台）。Ctrl+C 一起停。
# 常用开关（写在命令前面）：
#   MIC=0          不开麦克风，只用控制台打字
#   MUTE=1         不出声（测试用）
#   HEAD_IP=<头部IP>                 接机器人头：自动启动头部控制程序，并改用机器人自己的麦克风收音
#   HEAD_URL=http://127.0.0.1:8787   只接头部控制程序、不自动启动（已经手动启动时用）
#   TTS_PROVIDER=dashscope           语音合成改用百炼（更快）
#   CHAT_MODEL=qwen-plus             对话改用 qwen-plus（更稳但慢一倍）
set -e
cd "$(dirname "$0")"
export KEYS_FILE="${KEYS_FILE:-$(cd .. && pwd)/.keys.env}"     # 本地密钥文件，不在仓库里
PY="${PYTHON:-python3}"                                         # 需要装了 numpy、sounddevice、funasr、requests 的 Python
if [ ! -f "$KEYS_FILE" ]; then echo "找不到密钥文件 $KEYS_FILE（需要 DASHSCOPE_API_KEY，可选 STEPFUN_API_KEY / STEPFUN_BASE_URL）"; exit 1; fi
set -a; . "$KEYS_FILE"; set +a
# 本机这几个程序互相调用、调国内接口都直连：清掉终端里的代理设置（SOCKS 代理会让本机请求直接报错）
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
export NO_PROXY="localhost,127.0.0.1,::1" no_proxy="localhost,127.0.0.1,::1"

python3 memory/memory_server.py &
MEM=$!
PIDS="$MEM"
trap 'kill $PIDS 2>/dev/null' EXIT

# 接机器人头：启动孙博的头部控制程序（AI-Lover 仓库 hardware/head_motion_bridge.py）
if [ -n "${HEAD_IP:-}" ]; then
  BRIDGE="${HEAD_BRIDGE:-$(cd .. && pwd)/partner/ai-lover/hardware/head_motion_bridge.py}"
  if [ ! -f "$BRIDGE" ]; then echo "找不到头部控制程序 $BRIDGE（用 HEAD_BRIDGE 指定）"; exit 1; fi
  adb connect "$HEAD_IP:5555" >/dev/null || true
  # 这台头部的声卡：喇叭接在耳机输出上（要选 SPK_HP），只支持 44100Hz
  HEAD_MOTION_TRANSPORT=adb HEAD_MOTION_DRY_RUN=0 HEAD_MOTION_ADB_SERIAL="$HEAD_IP:5555" \
  HEAD_AUDIO_PLAYBACK_SETUP="tinymix -D {card} 'Playback Path' SPK_HP" HEAD_AUDIO_PLAYBACK_SAMPLE_RATE=44100 \
    "$PY" "$BRIDGE" &
  PIDS="$PIDS $!"
  export HEAD_URL="http://127.0.0.1:8787" HEAD_ADB_SERIAL="$HEAD_IP:5555" MIC_SOURCE="${MIC_SOURCE:-head}"
  for i in $(seq 1 15); do curl -s --noproxy '*' -m 2 "$HEAD_URL/health" >/dev/null && break; sleep 1; done
fi
sleep 1
echo "打开演示控制台：http://127.0.0.1:${CONSOLE_PORT:-8910}"
[ "${NO_OPEN:-0}" = "1" ] || (sleep 3; open "http://127.0.0.1:${CONSOLE_PORT:-8910}" 2>/dev/null || true) &
cd brain && "$PY" brain.py
