#!/bin/bash
# 在 Mac 当前连的网络里找机器人头（开着 adb 5555 端口、型号是 LubanCat 的那台），打印它的 IP。
# 先试上次找到的地址，找不到再扫整个网段。找不到时什么都不打印，返回 1。
CACHE="$(dirname "$0")/../.head_ip"
is_head() {
  adb connect "$1:5555" >/dev/null 2>&1
  perl -e "alarm shift; exec @ARGV" 5 adb -s "$1:5555" shell getprop ro.product.model 2>/dev/null | grep -qi lubancat
}
if [ -f "$CACHE" ]; then
  last=$(cat "$CACHE")
  if nc -z -G 1 "$last" 5555 2>/dev/null && is_head "$last"; then echo "$last"; exit 0; fi
fi
for net in $(ifconfig | grep -oE "inet (172|192|10)\.[0-9]+\.[0-9]+\.[0-9]+" | awk '{print $2}' | cut -d. -f1-3 | sort -u); do
  found=$(for i in $(seq 1 254); do ( nc -z -G 1 "$net.$i" 5555 2>/dev/null && echo "$net.$i" ) & done; wait)
  for ip in $found; do
    if is_head "$ip"; then echo "$ip" > "$CACHE"; echo "$ip"; exit 0; fi
  done
done
exit 1
