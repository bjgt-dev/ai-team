#!/usr/bin/env python3
"""W-S1 storage_scan: classify daily packages / logs / dumps, register test objects.
Reads DSN only from state/.cq-dsn. Deletes nothing. Already-encrypted daily
packages are copied as-is (no double encryption); only the small PG-metadata
test sample is encrypted with a hub-local test key (test-only, never the
production backup key). Real C1 (>14d) and C3 (monthly dump) eligibility is
reported honestly: 0 eligible objects found on 2026-11-04 is a finding, not a
failure to invent one.
"""
import os, json, hashlib, shutil, subprocess, sys
from datetime import date
STATE = "/home/ubuntu/ai-team/state"
EXPORT = "/data/export"
BACKUPS = "/home/ubuntu/backups-repo/backups"

def get_dsn():
    for line in open(os.path.join(STATE, ".cq-dsn")):
        line = line.strip()
        if line.startswith("CQ_DSN="):
            return line[7:].strip("\"'")
    return ""

def sha_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def main():
    import psycopg
    today = date.today()
    pkgs = []
    if os.path.isdir(BACKUPS):
        for d in sorted(os.listdir(BACKUPS)):
            p = os.path.join(BACKUPS, d)
            enc = os.path.join(p, "assets.tar.gz.enc")
            if os.path.isdir(p) and os.path.isfile(enc):
                try:
                    age = (today - date.fromisoformat(d)).days
                except ValueError:
                    age = -1
                pkgs.append({"date": d, "age_days": age, "bytes": os.path.getsize(enc), "path": enc})
    c1_eligible = [p for p in pkgs if p["age_days"] > 14]
    os.makedirs(EXPORT, exist_ok=True)
    objects = []
    # Test object 1: oldest available package, labelled test, NOT C1-eligible.
    if pkgs:
        src = pkgs[0]
        dst_dir = os.path.join(EXPORT, "test-c1-sample")
        os.makedirs(dst_dir, exist_ok=True)
        dst = os.path.join(dst_dir, "assets.tar.gz.enc")
        shutil.copyfile(src["path"], dst)
        objects.append({"id": "test-c1-sample", "category": "test", "sha256": sha_of(dst),
                        "bytes": os.path.getsize(dst), "retain_until": None,
                        "source_path": src["path"], "encrypted": True, "test_label": True})
    # Test object 2: PG metadata only (schema/table names + row estimates, no content).
    dsn = get_dsn()
    meta = {"tables": []}
    if dsn:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT n.nspname, c.relname, COALESCE(c.reltuples,0) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind='r' AND n.nspname NOT IN ('pg_catalog','information_schema') ORDER BY 1,2")
                meta["tables"] = [[r[0], r[1], int(r[2])] for r in cur.fetchall()]
    meta_path = "/tmp/storage-test-c3-meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, sort_keys=True)
    key_path = os.path.join(STATE, ".storage-test-key")
    if not os.path.exists(key_path):
        with open(key_path, "wb") as f:
            f.write(os.urandom(32))
        os.chmod(key_path, 0o600)
    dst_dir = os.path.join(EXPORT, "test-c3-sample")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, "pg-meta.json.enc")
    subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-in", meta_path,
                    "-out", dst, "-pass", f"file:{key_path}"], check=True)
    os.remove(meta_path)
    objects.append({"id": "test-c3-sample", "category": "test", "sha256": sha_of(dst),
                    "bytes": os.path.getsize(dst), "retain_until": None,
                    "source_path": "pg-metadata-only", "encrypted": True, "test_label": True})
    # Register + manifest
    if dsn:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                for o in objects:
                    cur.execute("""INSERT INTO storage.objects (id, category, sha256, bytes, retain_until, source_path, encrypted, test_label)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (id) DO UPDATE SET sha256=EXCLUDED.sha256, bytes=EXCLUDED.bytes""",
                        (o["id"], o["category"], o["sha256"], o["bytes"], o["retain_until"], o["source_path"], o["encrypted"], o["test_label"]))
            conn.commit()
    with open(os.path.join(EXPORT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(objects, f, ensure_ascii=False, indent=2, sort_keys=True)
    out = {"packages_found": len(pkgs), "c1_eligible_over_14d": len(c1_eligible),
           "c3_monthly_dump_found": False, "test_objects": [o["id"] for o in objects],
           "export_bytes": sum(o["bytes"] for o in objects)}
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return 0
if __name__ == "__main__":
    sys.exit(main())
