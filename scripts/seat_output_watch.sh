#!/bin/bash
# 席位产出看门狗：大管家席位超过阈值无新产出时输出 STALE 并写告警行。
# 检查面：ceo-seat/out/ 最新文件、patrol-latest.md、brain-active.log 三者取最近。
# 用法：seat_output_watch.sh [阈值分钟，默认 30]
THRESH_MIN="${1:-30}"
BASE=/home/ubuntu/ai-team
now=$(date +%s)
latest=0
for p in "$BASE/ceo-seat/out" "$BASE/ceo-seat/out/patrol-latest.md" "$BASE/state/brain-active.log"; do
  if [ -d "$p" ]; then
    t=$(find "$p" -type f -printf '%T@\n' 2>/dev/null | sort -n | tail -1 | cut -d. -f1)
  elif [ -f "$p" ]; then
    t=$(stat -c %Y "$p" 2>/dev/null)
  else
    t=0
  fi
  [ -n "$t" ] && [ "$t" -gt "$latest" ] && latest=$t
done
age_min=$(( (now - latest) / 60 ))
if [ "$age_min" -ge "$THRESH_MIN" ]; then
  echo "STALE last_output_age_min=$age_min threshold=$THRESH_MIN"
  echo "$(date '+%F %T') STALE age=${age_min}m" >> "$BASE/state/seat-output-alerts.log"
  exit 1
else
  echo "OK last_output_age_min=$age_min threshold=$THRESH_MIN"
  exit 0
fi
