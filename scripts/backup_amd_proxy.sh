#!/bin/bash
# amd-proxy（车队机）配置快照：每晚由枢纽拉取，落 state/ 随枢纽备份离站（2026-10-24 Twt 建，补双机恢复覆盖缺口）
set -u
OUT=/home/ubuntu/ai-team/state/amd-proxy-config-latest.tar.gz
TMP="${OUT}.tmp"
ssh -o ConnectTimeout=15 -o BatchMode=yes ubuntu@163.192.56.150 "sudo -n tar czf - --ignore-failed-read /var/lib/fanout /etc/systemd/system/fanout.service /etc/systemd/system/fanout-xray.service /home/ubuntu/.ssh/authorized_keys 2>/dev/null" > "$TMP" 2>/tmp/amd-proxy-backup.err
if [ -s "$TMP" ]; then
  mv "$TMP" "$OUT"
  echo "$(date '+%F %T') amd-proxy snapshot OK $(stat -c%s "$OUT") bytes" >> /home/ubuntu/ai-team/logs/backup.log
else
  echo "$(date '+%F %T') amd-proxy snapshot FAILED" >> /home/ubuntu/ai-team/logs/backup.log
fi
