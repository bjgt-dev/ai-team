#!/bin/bash
# cluster_watch.sh - 集群存活巡检（每 30 分钟）：查得到的直查，查不到的用队列活性推断，结果写 alerts.log
LOG=/home/ubuntu/ai-team/state/alerts.log
STAMP=$(date '+%F %T')
# 1) 网关容器（Docker 工业件）
docker ps --format '{{.Names}}' | grep -qi gateway || echo "[$STAMP] cluster-watch: gateway container NOT running" >> "$LOG"
# 2) 工人活性推断：pending/muse 里最老的契约滞留超过 6 小时且无 claimed 活动 => 工人未在拉单
OLDEST=$(find /home/ubuntu/ai-team/contracts/pending/muse -name '*.json' -mmin +10 2>/dev/null | head -1)
RECENT_CLAIM=$(find /home/ubuntu/ai-team/contracts/claimed /home/ubuntu/ai-team/contracts/done -mmin -10 2>/dev/null | head -1)
if [ -n "$OLDEST" ] && [ -z "$RECENT_CLAIM" ]; then
  echo "[$STAMP] cluster-watch: worker inactivity suspected - oldest pending $(basename "$OLDEST") >10m, no claim activity in 10m" >> "$LOG"
fi
# 3) 情报流水线：07:30 应产出，09:00 后仍无当日简报则告警
if [ "$(date +%H)" -ge 9 ]; then
  grep -q "$(date +%F)" /home/ubuntu/ai-team/state/intel_latest.md 2>/dev/null || echo "[$STAMP] cluster-watch: intel brief missing for today" >> "$LOG"
fi
echo "[$STAMP] cluster-watch: done"
