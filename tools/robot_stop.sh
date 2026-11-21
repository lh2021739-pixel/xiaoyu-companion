#!/bin/bash
# 关闭整套机器人演示：Mac 上的大脑、记忆库、头部控制程序，以及机器人上的录音。
# 用法：tools/robot_stop.sh            （可选：HEAD_IP=机器人地址）
for port in 8910 8900 8787; do
  pids=$(lsof -tiTCP:$port -sTCP:LISTEN 2>/dev/null)
  [ -n "$pids" ] && kill $pids 2>/dev/null
done
sleep 1
for port in 8910 8900 8787; do
  pids=$(lsof -tiTCP:$port -sTCP:LISTEN 2>/dev/null)
  [ -n "$pids" ] && kill -9 $pids 2>/dev/null
done
pkill -f "exec-out.*tinycap" 2>/dev/null

# 机器人上：停掉录音，关上麦克风通道（连不上就跳过）
heads="${HEAD_IP:+$HEAD_IP:5555} $(adb devices 2>/dev/null | awk '/:5555[[:space:]]+device$/{print $1}')"
for s in $(echo $heads | tr ' ' '\n' | sort -u); do
  perl -e "alarm shift; exec @ARGV" 5 adb -s "$s" shell "pkill tinycap; tinymix -D 0 'Capture MIC Path' 'MIC OFF'" >/dev/null 2>&1 || true
done
echo "已全部关闭：大脑、记忆库、头部控制程序、机器人录音。"
