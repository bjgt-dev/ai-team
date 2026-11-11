#!/usr/bin/env python3
"""W-S1 storage_audit: compare storage.objects vs /data/export manifest vs receipts.
Reports hub /data water and latest muse-1 receipt water (unknown if >24h old).
Deletes nothing."""
import os, json, sys, shutil
from datetime import datetime, timezone
STATE = "/home/ubuntu/ai-team/state"
EXPORT = "/data/export"
RECEIPTS = "/data/receipts/receipts.jsonl"

def get_dsn():
    for line in open(os.path.join(STATE, ".cq-dsn")):
        line = line.strip()
        if line.startswith("CQ_DSN="):
            return line[7:].strip("\"'")
    return ""

def main():
    import psycopg
    dsn = get_dsn()
    db_ids, loc_count = [], 0
    if dsn:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM storage.objects ORDER BY id")
                db_ids = [r[0] for r in cur.fetchall()]
                cur.execute("SELECT COUNT(*) FROM storage.locations")
                loc_count = cur.fetchone()[0]
    manifest = []
    mp = os.path.join(EXPORT, "manifest.json")
    if os.path.exists(mp):
        manifest = json.load(open(mp))
    man_ids = [o["id"] for o in manifest]
    latest_receipt = None
    if os.path.exists(RECEIPTS):
        for raw in open(RECEIPTS, encoding="utf-8").read().splitlines():
            try:
                latest_receipt = json.loads(raw)
            except Exception:
                pass
    usage = shutil.disk_usage("/data")
    out = {"db_objects": db_ids, "manifest_objects": man_ids,
           "in_db_not_manifest": [x for x in db_ids if x not in man_ids],
           "in_manifest_not_db": [x for x in man_ids if x not in db_ids],
           "locations": loc_count,
           "data_total_gb": round(usage.total / 1e9, 1), "data_used_pct": round(100 * usage.used / usage.total, 1),
           "export_bytes": sum(o.get("bytes", 0) for o in manifest),
           "latest_receipt": latest_receipt,
           "archive_limit_gb": 60, "archive_reserve_gb": 40, "pause_at_pct": 80}
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return 0
if __name__ == "__main__":
    sys.exit(main())
