#!/usr/bin/env python3
"""decision-queue-guard.py -- cutover51 W2.1: decision queue is DB-only.

Checks, every run (cron every 5 min):
  1. The two legacy JSON files are still archived read-only (mode 444) and
     their sha256 matches the recorded archive sha (no silent resurrection).
  2. No NEW writable decision-queue.json has appeared anywhere under the hub
     (a reappearing JSON is an alert: writes must go through the /decision API).
  3. DB decisions rows are schema-valid: pending rows have non-empty text and
     a source_id; no row has status outside (pending|resolved|closed).
Writes an outbox alert on any violation; exit 1 on violation, else 0.
Usage: python3 scripts/decision-queue-guard.py [--outbox]
"""
import glob
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

BASE = os.path.expanduser("~/ai-team")
STATE = os.path.join(BASE, "state")
OUTBOX = os.path.join(STATE, "outbox")
LOG = os.path.join(STATE, "logs", "decision-queue-guard.log")

LEGACY = [
    os.path.join(STATE, "decision-queue.json"),
    os.path.join(BASE, "ceo-seat", "state", "decision-queue.json"),
]
SHA_FILE = os.path.join(STATE, "logs", "decision-queue-archive.sha256")


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    outbox = "--outbox" in sys.argv
    problems = []
    recorded = {}
    try:
        for line in open(SHA_FILE):
            parts = line.split()
            if len(parts) >= 2:
                recorded[parts[1]] = parts[0]
    except Exception:
        problems.append("archive sha file missing: %s (run archive step first)" % SHA_FILE)
    for p in LEGACY:
        if not os.path.exists(p):
            problems.append("legacy file vanished (archive must stay): %s" % p)
            continue
        mode = oct(os.stat(p).st_mode & 0o777)
        if mode != "0o444":
            problems.append("legacy file not read-only (%s): %s" % (mode, p))
        cur = sha256(p)
        if p in recorded and recorded[p] != cur:
            problems.append("legacy file changed since archive: %s" % p)
    for p in glob.glob(os.path.join(BASE, "**", "decision-queue.json"), recursive=True):
        if p not in LEGACY:
            problems.append("NEW decision-queue.json appeared (writes must go via /decision API): %s" % p)
    try:
        dsn = ""
        for line in open(os.path.join(STATE, ".cq-dsn")):
            line = line.strip()
            if line.startswith("CQ_DSN="):
                dsn = line[len("CQ_DSN="):].strip().strip('"').strip("'")
                break
        import psycopg
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM public.decisions WHERE status NOT IN ('pending','resolved','closed')")
                bad_status = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM public.decisions WHERE status='pending' AND (text IS NULL OR text='' OR source_id IS NULL OR source_id='')")
                bad_pending = cur.fetchone()[0]
        if bad_status:
            problems.append("decisions rows with illegal status: %d" % bad_status)
        if bad_pending:
            problems.append("pending decisions rows missing text/source_id: %d" % bad_pending)
    except Exception as e:
        problems.append("DB check failed: %s" % str(e)[:160])
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write("%s problems=%d %s\n" % (ts, len(problems), "; ".join(problems)[:500]))
    if problems:
        print("decision-queue-guard FAIL: %d problem(s)" % len(problems))
        for p in problems:
            print(" - " + p)
        if outbox:
            os.makedirs(OUTBOX, exist_ok=True)
            path = os.path.join(OUTBOX, "decision-queue-guard-%s.md" % ts.replace(":", "").replace("-", ""))
            with open(path, "w") as f:
                f.write("# decision-queue-guard 告警\n\n时间(UTC): %s\n\n" % ts)
                for p in problems:
                    f.write("- %s\n" % p)
            print("alert written: " + path)
        return 1
    print("decision-queue-guard OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
