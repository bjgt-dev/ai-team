#!/bin/bash
# Window phase-2 pre-flight (READ-ONLY). Verifies current hub state before
# installing the central-queue claim/sweep timers.
# Prints one PASS/FAIL line per check plus a final verdict.
# Never prints DSN content, passwords, or keys.
set -u
BASE=/home/ubuntu/ai-team
FAIL=0
say() { # $1=PASS|FAIL|INFO $2=message
  echo "$1 $2"
  [ "$1" = "FAIL" ] && FAIL=1
}

# 1. Current crontab must equal the chair05 backup (no unrecorded change).
CUR_LINES=$(crontab -l 2>/dev/null | wc -l)
BAK_LINES=$(wc -l < "$BASE/state/crontab.bak-20261102-chair05" 2>/dev/null || echo "?")
CUR_HASH=$(crontab -l 2>/dev/null | sha256sum | cut -d' ' -f1)
BAK_HASH=$(sha256sum "$BASE/state/crontab.bak-20261102-chair05" 2>/dev/null | cut -d' ' -f1)
say INFO "crontab current_lines=$CUR_LINES backup_lines=$BAK_LINES"
if [ "$CUR_LINES" = "$BAK_LINES" ] && [ "$CUR_HASH" = "$BAK_HASH" ]; then
  say PASS "crontab matches chair05 backup"
else
  say FAIL "crontab differs from chair05 backup (STOP: investigate before editing)"
fi

# 2. Queue-watch line must still be present and uncommented (line 2 per backup).
L2=$(crontab -l 2>/dev/null | sed -n '2p')
case "$L2" in
  *queue_watch*) say PASS "crontab line2 is queue_watch (still active)" ;;
  *) say FAIL "crontab line2 is not queue_watch: [$L2]" ;;
esac

# 3. Patrol line must be present (line 10 per backup) - never touched.
L10=$(crontab -l 2>/dev/null | sed -n '10p')
case "$L10" in
  *patrol_det*) say PASS "crontab line10 is patrol_det (untouched)" ;;
  *) say FAIL "crontab line10 is not patrol_det: [$L10]" ;;
esac

# 4. cq-run wrapper must exist and be executable.
if [ -x "$BASE/scripts/cq-run-20261102.sh" ]; then
  say PASS "cq-run exists executable sha=$(sha256sum "$BASE/scripts/cq-run-20261102.sh" | cut -d' ' -f1 | cut -c1-16)"
else
  say FAIL "cq-run missing or not executable"
fi

# 5. cq-csv must exist.
if [ -f "$BASE/scripts/cq-csv-20261102.py" ]; then
  say PASS "cq-csv exists sha=$(sha256sum "$BASE/scripts/cq-csv-20261102.py" | cut -d' ' -f1 | cut -c1-16)"
else
  say FAIL "cq-csv missing"
fi

# 6. DSN file must exist, mode 600, non-empty (content never printed).
if [ -f "$BASE/state/.cq-dsn" ]; then
  MODE=$(stat -c %a "$BASE/state/.cq-dsn")
  SIZE=$(wc -c < "$BASE/state/.cq-dsn")
  if [ "$MODE" = "600" ] && [ "$SIZE" -gt 0 ]; then
    say PASS "cq-dsn exists mode=600 size=$SIZE"
  else
    say FAIL "cq-dsn mode/size unexpected mode=$MODE size=$SIZE"
  fi
else
  say FAIL "cq-dsn missing"
fi

# 7. Dispatcher timer state (report only; disable step handles it).
say INFO "dispatcher timer enabled=$(systemctl is-enabled openclaw-dispatcher.timer 2>&1) active=$(systemctl is-active openclaw-dispatcher.timer 2>&1)"

# 8. DB state via DSN (read-only counts; cq_timer has no DELETE).
if [ -f "$BASE/state/.cq-dsn" ]; then
  set -a; . "$BASE/state/.cq-dsn"; set +a
  CQ_DSN="$CQ_DSN" python3 - <<'PYEOF'
import os, sys
try:
    import psycopg
except ImportError:
    print("FAIL psycopg not available"); sys.exit(0)
dsn = os.environ.get("CQ_DSN", "")
try:
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, count(*) FROM central_tasks GROUP BY status ORDER BY status")
            print("INFO central_tasks " + " ".join(f"{s}={c}" for s, c in cur.fetchall()))
            cur.execute("SELECT count(*) FROM central_leases")
            print("INFO central_leases total=" + str(cur.fetchone()[0]))
            # central_events is INSERT-only for cq_timer by design (no SELECT);
            # its count (7, from the import) was verified via socket superuser
            # at import time and is not re-checked through this role.
            print("INFO central_events not SELECT-readable via cq_timer by design (skipped)")
            cur.execute("SELECT count(*) FROM central_tasks WHERE status='READY'")
            n = cur.fetchone()[0]
            print(("PASS" if n == 7 else "FAIL") + f" central_tasks READY={n} (expect 7)")
except Exception as e:
    print(f"FAIL db read error: {type(e).__name__}")
PYEOF
fi

if [ "$FAIL" = "0" ]; then echo "VERDICT ALL-PASS"; else echo "VERDICT STOP"; fi
