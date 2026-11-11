#!/usr/bin/env bash
# P0 baseline 2026-10-23: daily safe prune.
# Baseline says `docker system prune -af --volumes`; this wrapper deliberately
# does NOT delete volumes or stopped containers because the rollback container
# and state volumes are still inside the observation window. It only reclaims
# dangling images and build cache older than 7 days.
set -euo pipefail
LOG=/home/ubuntu/ai-team/logs/docker-prune.log
mkdir -p "$(dirname "$LOG")"
{
  echo "== $(date -Is) prune start"
  echo "protected stopped containers:"
  docker ps -a --filter status=exited --format '{{.Names}}\t{{.Status}}' || true
  docker image prune -f
  docker builder prune -f --filter until=168h
  echo "== $(date -Is) prune done"
} >> "$LOG" 2>&1
