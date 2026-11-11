#!/bin/bash
# alert_push.sh - 物理红警直推通道（2026-10-31 反偷懒令）
# 用法：alert_push.sh <KEY> "<消息>"
# 通道：Telegram（brain_watch.sh 里已有的告警通道，token/chat_id 从该文件读取，不打印、不外泄）
# 落盘：state/red-alerts.log + state/alerts.log，供审计；去重：同一 KEY 60 分钟内只推一次，FORCE=1 可强制重推（测试用）
set -u
KEY="${1:-GENERAL}"
MSG="${2:-red alert}"
BW="/home/ubuntu/ai-team/scripts/brain_watch.sh"
eval "TOKEN=$(grep -m1 '^TOKEN=' "$BW" 2>/dev/null | cut -d= -f2-)"
CHAT="$(grep -m1 'chat_id=' "$BW" 2>/dev/null | sed -n 's/.*chat_id=\([0-9]*\).*/\1/p')"
STATE_DIR="/home/ubuntu/ai-team/state"
DEDUP="$STATE_DIR/.red-alert-dedup.json"
NOW="$(date +%s)"
if [ "${FORCE:-0}" != "1" ]; then
  LAST="$(python3 -c "import json,sys; d=json.load(open('$DEDUP')) if __import__('os').path.exists('$DEDUP') else {}; print(d.get('$KEY',0))" 2>/dev/null || echo 0)"
  if [ "$((NOW - LAST))" -lt 3600 ]; then
    echo "DEDUP_SKIP $KEY age=$((NOW - LAST))s"
    exit 0
  fi
fi
python3 -c "import json,os; p='$DEDUP'; d=json.load(open(p)) if os.path.exists(p) else {}; d['$KEY']=$NOW; json.dump(d, open(p,'w'))" 2>/dev/null || true
TS="$(date '+%F %T')"
echo "$TS RED_ALERT $KEY $MSG" >> "$STATE_DIR/red-alerts.log"
echo "$TS RED_ALERT $KEY $MSG" >> "$STATE_DIR/alerts.log"
if [ -n "${TOKEN:-}" ] && [ -n "${CHAT:-}" ]; then
  RESP="$(curl -s -m 15 -X POST "https://api.telegram.org/bot$TOKEN/sendMessage" -d "chat_id=$CHAT" -d "text=🔴 $MSG" 2>&1)"
  echo "TG_PUSH $KEY resp_len=${#RESP} ok=$(echo "$RESP" | grep -o '\"ok\":true' || echo no)"
else
  echo "TG_PUSH $KEY NO_CHANNEL"
fi
