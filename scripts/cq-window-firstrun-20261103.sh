#!/bin/bash
# Window phase-2 first-run check (READ-ONLY except reading the log).
# Verifies the claim/sweep timers' first live runs on production:
# - cq-csv.log shows claim runs without errors
# - at least 1 task moved READY -> DISPATCHING/CLAIMED with a lease
# - no task lost (total still 7), no spurious QUARANTINED on fresh claims
# Never prints DSN content, passwords, or keys.
set -u
BASE=/home/ubuntu/ai-team
LOG="$BASE/state/logs/cq-csv.log"
echo "=== log tail (last 40 lines) ==="
tail -40 "$LOG" 2>&1 || echo "LOG-MISSING"
echo "=== log error scan ==="
if grep -Ei 'Traceback|ERROR|InsufficientPrivilege|exception|FATAL' "$LOG" 2>/dev/null | tail -5 | grep -q .; then
  echo "ERRORS-FOUND"
  grep -Ei 'Traceback|ERROR|InsufficientPrivilege|exception|FATAL' "$LOG" | tail -5
else
  echo "NO-ERRORS"
fi
if [ -f "$BASE/state/.cq-dsn" ]; then
  set -a; . "$BASE/state/.cq-dsn"; set +a
  CQ_DSN="$CQ_DSN" python3 - <<'PYEOF'
import os
import psycopg
dsn = os.environ.get("CQ_DSN", "")
with psycopg.connect(dsn) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT status, count(*) FROM central_tasks GROUP BY status ORDER BY status")
        rows = cur.fetchall()
        total = sum(c for _, c in rows)
        print("INFO central_tasks " + " ".join(f"{s}={c}" for s, c in rows) + f" total={total}")
        cur.execute("SELECT task_id, status, expires_at FROM central_leases ORDER BY expires_at")
        leases = cur.fetchall()
        print(f"INFO central_leases total={len(leases)}")
        for t, s, e in leases:
            print(f"INFO lease task={t} status={s} expires={e}")
        claimed = sum(c for s, c in rows if s in ("DISPATCHING", "CLAIMED"))
        bad = sum(c for s, c in rows if s in ("QUARANTINED",))
        if claimed >= 1 and total == 7 and bad == 0:
            print("VERDICT FIRST-RUN-PASS")
        else:
            print(f"VERDICT FIRST-RUN-FAIL claimed={claimed} total={total} quarantined={bad}")
PYEOF
fi
