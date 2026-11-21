#!/bin/bash
# 一键启动机器人演示：自动找机器人地址 → 启动头部控制程序、记忆库、大脑（用机器人麦克风）→ 打开控制台。
# 关闭：关掉这个窗口，或按 Ctrl+C，会把所有程序和机器人录音一起关掉。
# 用法：PYTHON=<装好依赖的Python> tools/robot_start.sh     （可选：HEAD_IP=机器人地址，跳过自动查找）
cd "$(dirname "$0")/.." || exit 1
TOOLS="$(pwd)/tools"

cleanup() {
  trap - EXIT HUP INT TERM
  echo; echo "正在关闭……"
  HEAD_IP="$HEAD_IP" "$TOOLS/robot_stop.sh" >/dev/null 2>&1
  echo "已全部关闭，可以关掉这个窗口了。"
  exit 0
}

echo "== 记忆陪伴机器人 · 一键启动 =="
"$TOOLS/robot_stop.sh" >/dev/null 2>&1          # 先清掉上次没关干净的

while [ -z "$HEAD_IP" ]; do
  echo "正在当前网络里找机器人……"
  HEAD_IP=$("$TOOLS/find_head.sh")
  if [ -z "$HEAD_IP" ]; then
    echo "没找到机器人。请确认：机器人已开机，Mac 和机器人连的是同一个手机热点。"
    read -r -p "按回车重新找；输入 m 再回车，就不接机器人、只用 Mac： " ans
    if [ "$ans" = "m" ]; then HEAD_IP=""; break; fi
  fi
done

trap cleanup EXIT HUP INT TERM
if [ -n "$HEAD_IP" ]; then
  echo "找到机器人：$HEAD_IP"
  HEAD_IP="$HEAD_IP" ./run.sh
else
  echo "只用 Mac 启动（Mac 麦克风和喇叭）"
  ./run.sh
fi
