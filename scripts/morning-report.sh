#!/bin/bash
# morning-report.sh - C3 晨报最小版 v3（2026-11-04，令12）：独立发送，无红点前缀，不走红警去重，不写 red-alerts.log，token 行取自 alert_push.sh 本机复制，不打印
set -u
BW="/home/ubuntu/ai-team/scripts/brain_watch.sh"
eval "TOKEN=$(grep -m1 '^TOKEN=' "$BW" 2>/dev/null | cut -d= -f2-)"
CHAT="$(grep -m1 'chat_id=' "$BW" 2>/dev/null | sed -n 's/.*chat_id=\([0-9]*\).*/\1/p')"
S="/home/ubuntu/ai-team/state"
PENDING="$(python3 -c "import json; print(len(json.load(open(\"$S/decision-queue.json\"))[\"pending\"]))" 2>/dev/null || echo "?")"
DELIV="$(find "$S/deliverables" -type f -mtime -1 2>/dev/null | wc -l | tr -d " ")"
TODAY="$(date "+%F")"; YEST="$(date -d "yesterday" "+%F" 2>/dev/null || date "+%F")"
ALERTS="$(grep -E "^$TODAY|^$YEST" "$S/alerts.log" 2>/dev/null | grep -c "RED_ALERT" || echo 0)"
# ORDER13-6 anomaly (only when present): failed C2 timer tasks in last 24h + pool gap flag
TIMER_FAIL=0
for _t in gateway_canary audit_gateway config_commit backup_restore_drill health_3q; do
  _latest="$(grep -l "^TIMER=$_t$" "$S/c2-task-results"/*.txt 2>/dev/null | xargs -r ls -t 2>/dev/null | head -1)"
  if [ -n "$_latest" ] && ! grep -q "^TIMER_RC=0$" "$_latest" 2>/dev/null; then TIMER_FAIL=$((TIMER_FAIL+1)); fi
done
POOL_GAP="$(python3 -c "import json; print('yes' if not json.load(open('$S/gateway-pool.json')).get('pool_minimum_2_met', True) else 'no')" 2>/dev/null || echo "?")"
ANOM=""
[ "${TIMER_FAIL:-0}" != "0" ] && ANOM="定时任务失败 ${TIMER_FAIL} 件；"
[ "$POOL_GAP" = "yes" ] && ANOM="${ANOM}可用池未达2个下限；"
MODELPOOL="$(python3 /home/ubuntu/ai-team/scripts/model-pool-line.py 2>/dev/null || echo 模型池未统计)"
FOUR_LINES="$(python3 /home/ubuntu/ai-team/scripts/four-lines-20261104.py 2>/dev/null || echo "ALERT24=24小时内 incident 未统计
FOUR=四数：未统计")"
ALERT24="$(echo "$FOUR_LINES" | sed -n "s/^ALERT24=//p")"
FOURTXT="$(echo "$FOUR_LINES" | sed -n "s/^FOUR=//p")"

MSG="晨报 $TODAY
昨夜产出：近 1 天交付物 ${DELIV} 件
需批：待决策 ${PENDING} 项（通常为 0，有 L3 另列）
异常：${ALERT24}${ANOM:+，异常补充：}${ANOM}
模型池：${MODELPOOL}
${FOURTXT}"
if [ "${DRY_RUN:-0}" = "1" ]; then
  printf "%s\n" "$MSG"
  exit 0
fi
echo "$(date "+%F %T") MORNING_REPORT pending=$PENDING deliv=$DELIV alerts=$ALERTS" >> "$S/morning-report.log"
if [ -n "${TOKEN:-}" ] && [ -n "${CHAT:-}" ]; then
  RESP="$(curl -s -m 15 -X POST "https://api.telegram.org/bot$TOKEN/sendMessage" -d "chat_id=$CHAT" -d "text=$MSG" 2>&1)"
  echo "TG_PUSH MORNING resp_len=${#RESP} ok=$(echo "$RESP" | grep -o "\"ok\":true" || echo no)"
else
  echo "TG_PUSH MORNING NO_CHANNEL"
fi
