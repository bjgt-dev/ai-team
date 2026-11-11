#!/bin/bash
# cq-run.sh — central-queue timer wrapper (DRAFT, NOT installed).
# Fail closed: without a readable DSN file it exits without doing anything.
# The DSN file is created by the operator at the switch window (mode 600,
# never committed to any repo or log): /home/ubuntu/ai-team/state/.cq-dsn
# containing one line: CQ_DSN="host=... port=... dbname=... user=..."
set -u
DSN_FILE=/home/ubuntu/ai-team/state/.cq-dsn
LOG=/home/ubuntu/ai-team/state/logs/cq-csv.log
if [ ! -r "$DSN_FILE" ]; then
  echo "$(date '+%F %T') cq-run: no readable DSN file, refusing" >> "$LOG" 2>/dev/null || true
  exit 0
fi
# shellcheck disable=SC1090
. "$DSN_FILE"
export CQ_DSN
exec python3 /home/ubuntu/ai-team/scripts/cq-csv-20261102.py "$@" >> "$LOG" 2>&1
