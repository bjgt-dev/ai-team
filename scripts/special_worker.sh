#!/usr/bin/env bash
# 特种 Worker 按需调起（方案 B，2026-10-20 董事长批复）
# claude-code 容器平时停机；特种任务到达才 start，跑完即 stop（trap 保证）。
# 资源紧箍咒每次启动前重申：--cpus=0.8 --memory=1g --memory-swap=1g，防死循环拖垮 VPS。
# 工作区 /workspace 是宿主机挂载（/opt/claude-code/workspace），产物天然回枢纽，不随容器停机丢失。
# 用法：special_worker.sh <prompt文件> [结果文件]
set -euo pipefail
PROMPT_FILE="${1:?need prompt file}"
RESULT_FILE="${2:-/home/ubuntu/ai-team/logs/special-$(date +%Y%m%d-%H%M).out}"
C=claude-code
docker update --cpus=0.8 --memory=1g --memory-swap=1g "$C" >/dev/null
docker start "$C" >/dev/null
cleanup() { docker stop -t 10 "$C" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker cp "$PROMPT_FILE" "$C":/tmp/special-prompt.txt
START=$(date +%s)
docker exec "$C" bash -lc 'cd /workspace && timeout 1200 claude -p "$(cat /tmp/special-prompt.txt)" --permission-mode acceptEdits --allowedTools "Bash,Read,Write,Edit,Glob,Grep"' | tee "$RESULT_FILE"
echo "SPECIAL_DONE elapsed=$(( $(date +%s) - START ))s result=$RESULT_FILE"
