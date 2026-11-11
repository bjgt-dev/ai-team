#!/bin/bash
# Install central-queue claim+sweep timers (switch window phase 2).
# Appends exactly 2 lines to the hub crontab. Reversible: remove the lines
# (rollback = restore state/crontab.bak-20261102-chair05).
# Fails closed on any unexpected state (does not install).
set -u
BASE=/home/ubuntu/ai-team
TMP=/tmp/cq-crontab-new-20261103
LINE1='* * * * * /home/ubuntu/ai-team/scripts/cron_flock.sh cq_claim /home/ubuntu/ai-team/scripts/cq-run-20261102.sh claim cq-timer'
LINE2='* * * * * /home/ubuntu/ai-team/scripts/cron_flock.sh cq_sweep /home/ubuntu/ai-team/scripts/cq-run-20261102.sh sweep'
[ -x "$BASE/scripts/cron_flock.sh" ] || { echo "FAIL cron_flock.sh missing/not executable"; exit 1; }
[ -x "$BASE/scripts/cq-run-20261102.sh" ] || { echo "FAIL cq-run missing/not executable"; exit 1; }
crontab -l > "$TMP" || { echo "FAIL cannot read crontab"; exit 1; }
if grep -Fxq "$LINE1" "$TMP" || grep -Fxq "$LINE2" "$TMP"; then
  echo "FAIL timer lines already present (refusing to duplicate)"; exit 1
fi
BEFORE=$(wc -l < "$TMP")
printf '%s\n%s\n' "$LINE1" "$LINE2" >> "$TMP"
AFTER=$(wc -l < "$TMP")
[ "$AFTER" = "$((BEFORE + 2))" ] || { echo "FAIL line count $BEFORE -> $AFTER (expect +2)"; exit 1; }
L2=$(sed -n '2p' "$TMP"); L10=$(sed -n '10p' "$TMP")
case "$L2" in *queue_watch*) ;; *) echo "FAIL line2 changed: [$L2]"; exit 1;; esac
case "$L10" in *patrol_det*) ;; *) echo "FAIL line10 changed: [$L10]"; exit 1;; esac
crontab "$TMP" || { echo "FAIL crontab install failed"; exit 1; }
echo "PASS timers installed lines $BEFORE -> $(crontab -l | wc -l)"
crontab -l | tail -2
rm -f "$TMP"
