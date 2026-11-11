#!/usr/bin/env python3
"""kb-index-sync: scan ~/ai-team/knowledge/ for *.md and upsert public.kb_index.

file_id = path relative to knowledge/ ; path = absolute path ;
updated_at = file mtime.  Upsert only; never deletes rows (stale entries
stay until a human reviews them).

DB access, in order:
  1. $GATE_DB_DSN or /etc/noesis/gate-db.env (DATABASE_URL/GATE_DB_DSN/PG* vars)
     via psycopg (only when the file/env is present -- no credentials are
     created or stored by this script);
  2. `docker exec ops-pg psql` (plain, then `sudo -n docker exec ...`),
     the existing hub convention for scripts -- no credential needed.

Exit 0 on success; non-zero with a message on stderr on failure.
"""
import os
import subprocess
import sys
from pathlib import Path

KNOWLEDGE = Path("/home/ubuntu/ai-team/knowledge")
DB = "central_queue"


def _parse_env_file(path):
    vals = {}
    try:
        for line in Path(path).read_text(errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return vals


def _dsn_from_env():
    if os.environ.get("GATE_DB_DSN"):
        return os.environ["GATE_DB_DSN"]
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    for p in ("/etc/noesis/gate-db.env", "/etc/noesis/db-kb_sync.env"):
        vals = _parse_env_file(p)
        if vals.get("GATE_DB_DSN") or vals.get("DATABASE_URL"):
            return vals.get("GATE_DB_DSN") or vals.get("DATABASE_URL")
        pw = vals.get("PGPASSWORD") or vals.get("DB_PASSWORD")
        user = vals.get("PGUSER") or vals.get("DB_USER")
        if pw and user:
            return (
                "host={host} port={port} dbname={db} user={user} password={pw}".format(
                    host=vals.get("PGHOST") or vals.get("DB_HOST", "172.17.0.3"),
                    port=vals.get("PGPORT") or vals.get("DB_PORT", "5432"),
                    db=vals.get("PGDATABASE") or vals.get("DB_NAME", DB),
                    user=user,
                    pw=pw,
                )
            )
    return None


def _sql_lit(s):
    return "'" + s.replace("'", "''") + "'"


def _run_psql(sql):
    """Run SQL via docker exec psql; return (rc, stdout, stderr)."""
    base = ["docker", "exec", "-i", "ops-pg", "psql", "-U", "postgres",
            "-d", DB, "-v", "ON_ERROR_STOP=1", "-A", "-t"]
    for cmd in (base, ["sudo", "-n"] + base):
        try:
            r = subprocess.run(cmd, input=sql, capture_output=True,
                               text=True, timeout=180)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return 1, "", str(e)
        if r.returncode == 0:
            return r.returncode, r.stdout, r.stderr
        last = r
        # permission problems -> try sudo variant; other errors -> stop
        if "permission denied" not in (r.stderr or "").lower():
            return r.returncode, r.stdout, r.stderr
    return last.returncode, last.stdout, last.stderr


def _upsert_psycopg(rows):
    import psycopg
    dsn = _dsn_from_env()
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO kb_index(file_id, path, updated_at) "
                "VALUES (%s, %s, to_timestamp(%s)) "
                "ON CONFLICT (file_id) DO UPDATE SET "
                "path = EXCLUDED.path, updated_at = EXCLUDED.updated_at",
                rows,
            )
        conn.commit()


def _upsert_psql(rows):
    vals = ",\n".join(
        "(%s, %s, to_timestamp(%s))" % (_sql_lit(fid), _sql_lit(p), mtime)
        for fid, p, mtime in rows
    )
    sql = (
        "BEGIN;\n"
        "INSERT INTO kb_index(file_id, path, updated_at) VALUES\n" + vals +
        "\nON CONFLICT (file_id) DO UPDATE SET "
        "path = EXCLUDED.path, updated_at = EXCLUDED.updated_at;\n"
        "COMMIT;\n"
    )
    rc, out, err = _run_psql(sql)
    if rc != 0:
        raise RuntimeError("psql upsert failed rc=%s: %s" % (rc, err.strip()))


def main():
    if not KNOWLEDGE.is_dir():
        print("knowledge dir missing: %s" % KNOWLEDGE, file=sys.stderr)
        return 1
    rows = []
    for p in sorted(KNOWLEDGE.rglob("*.md")):
        if not p.is_file():
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        rows.append((p.relative_to(KNOWLEDGE).as_posix(), str(p), mtime))
    if not rows:
        print("kb-index-sync: no .md files found")
        return 0
    dsn = _dsn_from_env()
    if dsn:
        try:
            _upsert_psycopg(rows)
        except Exception as e:  # fall back to docker exec
            print("kb-index-sync: DSN path failed (%s); trying docker exec" % e,
                  file=sys.stderr)
            _upsert_psql(rows)
    else:
        _upsert_psql(rows)
    print("kb-index-sync: scanned=%d upserted=%d" % (len(rows), len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
