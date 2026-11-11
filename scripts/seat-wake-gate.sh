#!/bin/bash
# seat-wake-gate.sh -- 上岗硬门禁：没跑唤醒就不许开口答事实性问题
# 用法：seat-wake-gate.sh [最多几小时，默认 4]
# 检查 ~/workspace/.gates/wake.done（wake-up-check.sh 跑通后写，带 epoch 时间戳）：
#   文件不存在 或 超过 N 小时未更新 即 exit 1，并提示跑唤醒。
# 每次结果记入 ~/workspace/tools/seat-wake-gate-ledger.jsonl。
# 换席位、换 VM、隔夜都必须重跑唤醒。
set -u
MAX_HOURS="${1:-4}"
MARKER="$HOME/workspace/.gates/wake.done"
LEDGER="$HOME/workspace/tools/seat-wake-gate-ledger.jsonl"
NOW=$(date +%s)
fail=""
if [ ! -f "$MARKER" ]; then
  fail="唤醒没跑过（缺 $MARKER）"
else
  EPOCH=$(grep '^epoch=' "$MARKER" 2>/dev/null | cut -d= -f2)
  if [ -z "$EPOCH" ]; then
    fail="唤醒标记无时间戳"
  elif [ $((NOW - EPOCH)) -gt $((MAX_HOURS * 3600)) ]; then
    fail="唤醒已过期（$(( (NOW - EPOCH) / 60 )) 分钟前，限 ${MAX_HOURS} 小时）"
  fi
fi
mkdir -p "$(dirname "$LEDGER")"
if [ -n "$fail" ]; then
  printf '{"ts":"%s","result":"FAIL","reason":"%s"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$fail" >> "$LEDGER"
  echo "GATE FAIL：$fail，先跑 bash ~/workspace/tools/wake-up-check.sh 再开口"
  exit 1
fi
printf '{"ts":"%s","result":"PASS","age_min":%s}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(( (NOW - EPOCH) / 60 ))" >> "$LEDGER"
echo "GATE PASS：唤醒 $(( (NOW - EPOCH) / 60 )) 分钟前跑过，可以开口"
exit 0
