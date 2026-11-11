#!/usr/bin/env python3
"""health_3q reporter (U-18, 2026-11-10): 变色才报.

Runs health_3q-20261104.py directly (independent of the timer queue, whose
30-min submits are currently often SKIPped by cron_flock all_slots_busy),
compares its exit code with the previous run stored in
state/health_3q_last_exit.json, and writes an outbox alert ONLY when the
exit code changed. Same exit code twice in a row -> silent (one log line).

Outbox file: state/outbox/health-3q-<UTC timestamp>.md
State file:  state/health_3q_last_exit.json  {"exit_code": N, "ts": ..., "q1": ...}
Log:         state/logs/health_3q_reporter.log (one line per run)

First run with no previous state only alerts when exit != 0 (baseline).
Exit code of this script itself is always 0 (reporter, not a gate).
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

BASE = "/home/ubuntu/ai-team"
STATE = os.path.join(BASE, "state")
LAST = os.path.join(STATE, "health_3q_last_exit.json")
LOG = os.path.join(STATE, "logs", "health_3q_reporter.log")
OUTBOX = os.path.join(STATE, "outbox")
HEALTH_3Q = os.path.join(BASE, "scripts", "health_3q-20261104.py")


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_last():
    try:
        with open(LAST) as f:
            return json.load(f)
    except Exception:
        return None


def save_last(exit_code, detail):
    tmp = LAST + ".tmp"
    rec = {"exit_code": exit_code, "ts": utc_now(),
           "q1_usable": detail.get("q1_usable"),
           "q2_working": detail.get("q2_working"),
           "q3_correct": detail.get("q3_correct")}
    with open(tmp, "w") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    os.replace(tmp, LAST)
    return rec


def write_outbox(exit_code, prev_code, detail):
    os.makedirs(OUTBOX, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(OUTBOX, "health-3q-%s.md" % stamp)
    q1 = detail.get("q1", [])
    q1_str = "\n".join("- %s: %s" % (k, v) for k, v in q1)
    body = ("# health_3q 变色告警\n\n"
            "- 时间(UTC): %s\n- exit 码: %s -> %s\n"
            "- Q1 能用: %s; Q2 在做事: %s; Q3 对吗: %s\n\n"
            "## Q1 明细\n%s\n\n"
            "## Q2\n%s\n\n## Q3\n%s\n\n"
            "## notes\n%s\n"
            % (utc_now(), prev_code, exit_code,
               detail.get("q1_usable"), detail.get("q2_working"),
               detail.get("q3_correct"), q1_str,
               detail.get("q2"), detail.get("q3"), detail.get("notes")))
    with open(path, "w") as f:
        f.write(body)
    return path


def maybe_alert(exit_code, detail):
    """Compare with last run; write outbox only on exit-code change.

    Returns (alerted: bool, prev_code). First run + exit 0 -> silent baseline.
    """
    last = load_last()
    prev_code = last.get("exit_code") if isinstance(last, dict) else None
    changed = (prev_code is None) or (prev_code != exit_code)
    save_last(exit_code, detail)
    if not changed:
        return False, prev_code
    if prev_code is None and exit_code == 0:
        return False, prev_code
    path = write_outbox(exit_code, prev_code, detail)
    return True, prev_code


def write_heartbeat(ok, detail):
    """Cutover51 W4: the reporter's own heartbeat (a dead reporter must go
    red via the heartbeats view). Best effort, never fatal."""
    try:
        dsn = ""
        for line in open(os.path.join(BASE, "state/.cq-dsn")):
            line = line.strip()
            if line.startswith("CQ_DSN="):
                dsn = line[len("CQ_DSN="):].strip().strip('"').strip("'")
                break
        if not dsn:
            return
        import psycopg
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO public.heartbeats(probe, ok, detail) VALUES (%s, %s, %s)",
                            ("health_3q_reporter", bool(ok), str(detail)[:500]))
            conn.commit()
    except Exception:
        pass


def main():
    r = subprocess.run(["python3", HEALTH_3Q], capture_output=True,
                       text=True, timeout=300)
    try:
        detail = json.loads(r.stdout)
    except Exception:
        detail = {"raw": (r.stdout or "")[-500:], "stderr": (r.stderr or "")[-300:]}
    alerted, prev = maybe_alert(r.returncode, detail)
    write_heartbeat(True, "exit=%s alerted=%s" % (r.returncode, alerted))
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write("%s exit=%s prev=%s alerted=%s q1=%s q2=%s q3=%s\n"
                % (utc_now(), r.returncode, prev, alerted,
                   detail.get("q1_usable"), detail.get("q2_working"),
                   detail.get("q3_correct")))
    print(json.dumps({"exit_code": r.returncode, "prev_exit_code": prev,
                      "alerted": alerted, "q1_usable": detail.get("q1_usable")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
