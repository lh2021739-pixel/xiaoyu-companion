#!/bin/bash
# 双击启动记忆陪伴机器人：自动找机器人地址，启动头部控制程序、记忆库、大脑，打开控制台。
# 关掉这个窗口（或按 Ctrl+C）就会全部关闭。
# 需要：Python 3.12 及以下（装好 requirements.txt）、adb、仓库外的 ../.keys.env。
# 用别的 Python：在终端里先 export PYTHON=/path/to/python，或改下面这一行。
cd "$(dirname "$0")/.." || exit 1
export PYTHON="${PYTHON:-python3}"
exec tools/robot_start.sh
