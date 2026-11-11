#!/usr/bin/env python3
"""W-S1 storage_confirm: apply /data/receipts/receipts.jsonl to storage.locations/receipts.
Idempotent via processed-line offset file. Sha mismatch => log only, no location row.
"""
import os, json, sys
STATE = "/home/ubuntu/ai-team/state"
RECEIPTS = "/data/receipts/receipts.jsonl"
OFFSET = os.path.join(STATE, "storage-confirm-offset.txt")

def get_dsn():
    for line in open(os.path.join(STATE, ".cq-dsn")):
        line = line.strip()
        if line.startswith("CQ_DSN="):
            return line[7:].strip("\"'")
    return ""

def main():
    import psycopg
    offset = 0
    if os.path.exists(OFFSET):
        try:
            offset = int(open(OFFSET).read().strip() or "0")
        except ValueError:
            offset = 0
    lines = []
    if os.path.exists(RECEIPTS):
        lines = open(RECEIPTS, encoding="utf-8").read().splitlines()
    new = lines[offset:]
    applied, skipped = 0, 0
    dsn = get_dsn()
    if dsn and new:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                for raw in new:
                    try:
                        r = json.loads(raw)
                    except Exception:
                        skipped += 1; continue
                    cur.execute("SELECT sha256 FROM storage.objects WHERE id=%s", (r.get("object_id"),))
                    row = cur.fetchone()
                    if not row or row[0].lower() != str(r.get("sha256", "")).lower():
                        skipped += 1; continue
                    cur.execute("""INSERT INTO storage.receipts (object_id, node, sha256, bytes)
                        VALUES (%s,%s,%s,%s) ON CONFLICT (object_id, node, sha256) DO NOTHING""",
                        (r["object_id"], r["node"], str(r["sha256"]).lower(), int(r["bytes"])))
                    cur.execute("""INSERT INTO storage.locations (object_id, node, path, verified_at)
                        VALUES (%s,%s,%s, now())
                        ON CONFLICT (object_id, node) DO UPDATE SET path=EXCLUDED.path, verified_at=now()""",
                        (r["object_id"], r["node"], r["path"]))
                    applied += 1
            conn.commit()
    with open(OFFSET, "w") as f:
        f.write(str(len(lines)))
    print(json.dumps({"receipts_total": len(lines), "applied": applied, "skipped": skipped}, sort_keys=True))
    return 0
if __name__ == "__main__":
    sys.exit(main())
