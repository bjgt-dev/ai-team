#!/bin/bash
# DEPRECATED 2026-10-28: 空变量拉单脚本，已废弃，不得作为主链；原版备份见 worker_pull.sh.deprecated-20261028，替代链见 worker_pull_stage_a.sh / worker_pull_stage_b.sh
# Worker pull loop (2026-10-25 v2): replaces crontab dependency.
# Worker VM runs this; every 300s it pulls a task from the hub and executes it, then POSTs a receipt.
# Hub endpoint: http://127.0.0.1:18099/pull_task  (tailscale only, not public)
# Receipt endpoint: http://127.0.0.1:18099/receipt
HUB=http://127.0.0.1:18099
while true; do
  TASK=
  if [ -n  ] && [  != none ]; then
    echo  | bash 2>&1 | curl -fsS --max-time 30 -X POST --data-binary @- /receipt >/dev/null 2>&1 || true
  fi
  sleep 300
done
