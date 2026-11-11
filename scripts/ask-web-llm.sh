#!/bin/bash
# ask-web-llm.sh — 专家入口过载检查点（定版 P1 登记项，2026-10-24 Twt 落地）
# 规则：调用 web_brain 前查 1 分钟负载/CPU 数；>70% 时返回排队（exit 3），不压入专家通道。
# 阈值可用环境变量 ASK_WEB_LLM_CPU_THRESHOLD 覆盖（百分数，默认 70）。
TH="${ASK_WEB_LLM_CPU_THRESHOLD:-70}"
read -r l1 _ < /proc/loadavg
NCPU=$(nproc)
PCT=$(awk -v l="$l1" -v n="$NCPU" 'BEGIN{printf "%d", (l/n)*100}')
if [ "$PCT" -ge "$TH" ]; then
  echo "{\"status\": \"queued\", \"reason\": \"cpu_overload\", \"cpu_pct\": $PCT, \"threshold\": $TH, \"retry_after_s\": 300}"
  exit 3
fi
cd /home/ubuntu/ai-team && exec python3 scripts/web_brain.py "$@"
