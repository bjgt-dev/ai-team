#!/bin/bash
# cron_flock.sh — cutover51 W3.5: per-job lock ONLY (no global 8-slot pool).
# 2026-10-28 dual-standard: never silently skip. Every invocation logs
# [RUN] (lock acquired) or [SKIP] (named lock busy) to stderr (cron
# redirects to the job log) and to cron_flock.log.
# Cutover51 change: the 8-slot global pool caused 1250 all_slots_busy SKIP
# on 2026-11-10 (watchdog skipped). Each job now holds ONLY its own named
# lock; jobs never steal each other's slots. A failed lock writes a skip
# receipt (state/logs/cron_flock_skip_receipts.jsonl); the 3rd consecutive
# skip for the same job escalates to state/outbox/.
set -e
name="$1"
shift
base=/home/ubuntu/ai-team/state/locks
logfile=/home/ubuntu/ai-team/logs/cron_flock.log
skipfile=/home/ubuntu/ai-team/state/logs/cron_flock_skip_receipts.jsonl
countfile="$base/${name}.skipcount"
mkdir -p "$base" "$(dirname "$logfile")" "$(dirname "$skipfile")"
log() { echo "[$(date '+%F %T')] cron_flock name=$name $*" | tee -a "$logfile" >&2; }
exec 9>"$base/${name}.lock"
if ! /usr/bin/flock -n 9; then
  _n=0; [ -f "$countfile" ] && _n=$(cat "$countfile" 2>/dev/null || echo 0)
  _n=$((_n + 1)); echo "$_n" > "$countfile"
  _ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  printf '{"ts":"%s","job":"%s","result":"skip","reason":"named_lock_busy","consecutive":%s,"cmd":"%s"}\n' "$_ts" "$name" "$_n" "$*" >> "$skipfile"
  log "[SKIP] reason=named_lock_busy consecutive=$_n cmd=$*"
  if [ "$_n" -ge 3 ]; then
    printf '# cron_flock skip escalation %s\n\nJob `%s` failed to acquire its lock 3 times in a row (last at %s, cmd: %s).\nOperator: cron_flock.sh (cutover51 W3.5 per-job lock).\n' "$_ts" "$name" "$_ts" "$*" > "/home/ubuntu/ai-team/state/outbox/cron-flock-skip-$name.md"
    log "[ESCALATE] job=$name consecutive=$_n outbox written"
  fi
  exit 0
fi
echo 0 > "$countfile"
log "[RUN] cmd=$*"
exec "$@"
