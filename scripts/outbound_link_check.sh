#!/bin/bash
# 发送前链接门（方案 A 的 E002 检测器）：从标准输入读文本，提取 URL 逐个实查，非 200 退出码为 1
TEXT=$(cat)
FAIL=0
for u in $(echo "$TEXT" | grep -oE "https?://[^ )\"]+" | sort -u); do
  code=$(curl -s -o /dev/null -w "%{http_code}" -m 15 -L "$u")
  echo "$code $u"
  [ "$code" != "200" ] && FAIL=1
done
exit $FAIL
