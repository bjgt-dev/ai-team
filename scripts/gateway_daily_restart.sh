#!/bin/bash
# 网关每日 04:00 计划内重启（2026-10-21 L3 会诊裁决）：队列空才重启，防慢增长触发守卫自退
LOG=/home/ubuntu/ai-team/state/gateway-restart.log
P=/home/ubuntu/ai-team/contracts/pending
C=/home/ubuntu/ai-team/contracts/claimed
if find $P $C -name '*.json' 2>/dev/null | grep -q .; then
  echo "$(date -Is) SKIP queue not empty" >> $LOG; exit 0
fi
if /home/ubuntu/ai-team/scripts/gateway_ctl.sh restart planned-daily >/dev/null 2>&1; then
  echo "$(date -Is) RESTARTED gateway via gateway_ctl.sh (queue empty)" >> $LOG
else
  rc=$?
  echo "$(date -Is) RESTART_FAILED rc=$rc via gateway_ctl.sh" >> $LOG
  exit $rc
fi
