#!/bin/bash
# Cloady free-app keepalive (2026-11-08). Free apps auto-pause after 7d without activity.
# Best-effort HTTP keepalive: curl origin + worker so the app sees traffic.
# Definitive keepalive via Cloady API: token rebuild-20261108 IS on hub (cloady-api-token.env), verified working 2026-11-19.
# Permanent free workspace xray-free-2 (EU1 xray + USW1 xray-2), deployed 2026-11-19; trial workspace workspace-yra8zf deleted same day.
# Next 7d auto-pause ~2026-11-26 if no activity.
LOG=/home/ubuntu/ai-team/logs/cloady-keepalive.log
echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') eu1=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://xray-oftaghdf-xray-free-2.cloady.io/) usw1=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://xray-2-okhltme4-xray-free-2.cloady.io/) worker=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://cloady-vless-proxy.tianzy2017.workers.dev/)" >> "$LOG"
