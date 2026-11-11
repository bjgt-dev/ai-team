#!/bin/bash
# Disable the old dispatch routes (switch window phase 2, after first-run PASS).
# 1. systemctl disable --now openclaw-dispatcher.timer (sudo -n fallback).
# 2. Comment crontab line 2 (queue_watch) - only if it is still queue_watch.
# Patrol (line 10) and the new cq timers must stay intact. Reversible:
# re-enable the timer, uncomment line 2 (chair05 backup also exists).
# Fails closed on any unexpected state (does not install a half change
# without reporting it; the timer and crontab steps are independent, so a
# failure in one does not block verifying the other).
set -u
BASE=/home/ubuntu/ai-team

# --- dispatcher timer ---
echo "--- dispatcher timer before: enabled=$(systemctl is-enabled openclaw-dispatcher.timer 2>&1) active=$(systemctl is-active openclaw-dispatcher.timer 2>&1)"
systemctl disable --now openclaw-dispatcher.timer 2>&1 || true
if [ "$(systemctl is-enabled openclaw-dispatcher.timer 2>&1)" != "disabled" ]; then
  echo "plain disable did not take, trying sudo -n"
  sudo -n systemctl disable --now openclaw-dispatcher.timer 2>&1 || true
fi
EN=$(systemctl is-enabled openclaw-dispatcher.timer 2>&1)
AC=$(systemctl is-active openclaw-dispatcher.timer 2>&1)
echo "--- dispatcher timer after: enabled=$EN active=$AC"
case "$EN" in disabled) echo "PASS dispatcher timer disabled" ;; *) echo "FAIL dispatcher timer still $EN" ;; esac
case "$AC" in inactive|failed) echo "PASS dispatcher timer not active ($AC)" ;; *) echo "FAIL dispatcher timer still $AC" ;; esac

# --- queue_watch crontab line ---
TMP=/tmp/cq-crontab-disable-20261103
crontab -l > "$TMP" || { echo "FAIL cannot read crontab"; exit 1; }
L2=$(sed -n '2p' "$TMP")
case "$L2" in
  *queue_watch*)
    if [ "${L2#\#}" != "$L2" ]; then
      echo "INFO line2 already commented, leaving as-is"
    else
      sed -i '2s/^/#DISABLED-20261103-CHAIR05 /' "$TMP"
      echo "INFO line2 commented"
    fi
    ;;
  *) echo "FAIL line2 is not queue_watch: [$L2]"; exit 1 ;;
esac
L10=$(sed -n '10p' "$TMP")
case "$L10" in *patrol_det*) echo "PASS patrol line10 intact" ;; *) echo "FAIL line10 changed: [$L10]"; exit 1 ;; esac
grep -q 'cq_claim' "$TMP" && grep -q 'cq_sweep' "$TMP" || { echo "FAIL cq timer lines missing"; exit 1; }
echo "PASS cq timer lines intact"
BEFORE=$(crontab -l | wc -l)
crontab "$TMP" || { echo "FAIL crontab install failed"; exit 1; }
AFTER=$(crontab -l | wc -l)
echo "PASS crontab lines $BEFORE -> $AFTER"
crontab -l | sed -n '2p;10p'
crontab -l | grep cq_
rm -f "$TMP"
