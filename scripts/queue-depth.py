#!/usr/bin/env python3
"""queue-depth.py — queue depth quick check (Thorsten Ball Rule 5, 2026-11-07).
Read-only: SELECT status counts from central_tasks and print them.
Usage: python3 scripts/queue-depth.py"""
import os, sys

BASE = "/home/ubuntu/ai-team"
DSN_FILE = BASE + "/state/.cq-dsn"

def get_dsn():
    try:
        with open(DSN_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("CQ_DSN="):
                    return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""

def main():
    dsn = get_dsn()
    if not dsn:
        print("queue-depth: NO DSN")
        return 1
    import psycopg
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, COUNT(*) FROM central_tasks GROUP BY status ORDER BY status")
            rows = cur.fetchall()
            total = sum(n for _, n in rows)
            print("queue depth: " + " | ".join("%s=%d" % (s, n) for s, n in rows) + " | TOTAL=%d" % total)
            cur.execute("SELECT payload->>'task_id', status FROM central_tasks WHERE status IN ('READY','CLAIMED') ORDER BY updated_at DESC LIMIT 10")
            print("active (READY/CLAIMED):")
            for tid, st in cur.fetchall():
                print("  %-40s %s" % (tid, st))
    return 0

if __name__ == "__main__":
    sys.exit(main())
