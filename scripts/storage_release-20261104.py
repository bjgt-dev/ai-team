#!/usr/bin/env python3
"""W-S1 storage_release: DRY-RUN ONLY. Computes what would move to /data/trash
under the dual-copy rule (>=2 verified locations besides hub, or ephemeral).
Deletes nothing in W-S1. Test objects are never releasable."""
import os, json, sys
STATE = "/home/ubuntu/ai-team/state"

def get_dsn():
    for line in open(os.path.join(STATE, ".cq-dsn")):
        line = line.strip()
        if line.startswith("CQ_DSN="):
            return line[7:].strip("\"'")
    return ""

def main():
    import psycopg
    dsn = get_dsn()
    would, blocked = [], []
    if dsn:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, category, test_label FROM storage.objects ORDER BY id")
                for oid, cat, test_label in cur.fetchall():
                    cur.execute("SELECT COUNT(*) FROM storage.locations WHERE object_id=%s", (oid,))
                    n = cur.fetchone()[0]
                    if test_label:
                        blocked.append({"id": oid, "reason": "test object, never releasable"})
                    elif n >= 2:
                        would.append({"id": oid, "verified_locations": n})
                    else:
                        blocked.append({"id": oid, "reason": f"only {n} verified locations, need >=2"})
    print(json.dumps({"dry_run": True, "deleted": 0, "would_release": would, "blocked": blocked}, ensure_ascii=False, sort_keys=True))
    return 0
if __name__ == "__main__":
    sys.exit(main())
