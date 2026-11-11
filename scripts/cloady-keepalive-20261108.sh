#!/bin/bash
# Cloady free-app keepalive (2026-11-08). Free apps auto-pause after 7d without activity.
# Best-effort HTTP keepalive: curl origin + worker so the app sees traffic.
# Definitive keepalive (wake/redeploy via Cloady API) needs the API token, which is NOT on the hub.
# Next expiry ~2026-11-14 (7d after 2026-11-07 deploy/last confirmed activity).
LOG=/home/ubuntu/ai-team/logs/cloady-keepalive.log
echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') origin=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://xray-t1c6xag-workspace-yra8zf.cloady.io/) worker=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://cloady-vless-proxy.tianzy2017.workers.dev/)" >> "$LOG"
