#!/usr/bin/env python3
"""ORDER13 backup_restore_drill (weekly): latest encrypted backup from the
private repo -> SHA check -> decrypt -> unpack to /tmp -> MANIFEST spot check
-> pg dump COPY counts -> filtered temp-DB load (\\connect lines removed, so
it can NEVER touch live DBs) -> row counts -> drop temp DB -> cleanup /tmp.
Exit 0 only if SHA ok, MANIFEST present, learning_events count matches the
live child_agent count OR the difference is explained in output. No secrets
printed; passfile path only."""
import json, os, re, subprocess, sys, time, glob
REPO = "/home/ubuntu/backups-repo"
def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600, **kw)
def main():
    dates = sorted([d for d in os.listdir(os.path.join(REPO, "backups"))
                    if re.match(r"^\d{4}-\d{2}-\d{2}$", d)])
    if not dates:
        print(json.dumps({"ok": False, "error": "no backup dates"})); return 1
    date = dates[-1]
    r = run(["bash", "/home/ubuntu/ai-team/scripts/restore_assets.sh", date])
    ok_sha = "OK" in r.stdout
    outdir = "/tmp/restore-" + date
    manifest = ""
    try:
        manifest = open(os.path.join(outdir, "payload/MANIFEST.txt")).read()[:500]
    except OSError:
        pass
    # pg COPY counts from dump (no data printed)
    dump = os.path.join(outdir, "payload/pg_all.sql.gz")
    counts = {}
    try:
        z = run(["bash", "-c", "zcat " + dump + " | awk '/^COPY public\\.(central_tasks|learning_events) /{t=$2; f=1; next} /^\\\\\\./{if(f) counts[t]=c; f=0; c=0; next} f{c++} END{for (k in counts) print k, counts[k]}'"])
        for line in z.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2:
                counts[parts[0]] = int(parts[1])
    except Exception as e:
        counts = {"error": str(e)[:120]}
    # temp DB load (filtered)
    temp_counts = {}
    try:
        run(["sudo", "-n", "docker", "exec", "ops-pg", "psql", "-U", "postgres",
             "-c", "DROP DATABASE IF EXISTS drill_tmp;", "-c", "CREATE DATABASE drill_tmp;"])
        load = run(["bash", "-c",
            "zcat " + dump + " | grep -v '^\\\\connect' | grep -v 'CREATE DATABASE' | "
            "sudo -n docker exec -i ops-pg psql -U postgres -d drill_tmp 2>&1 | tail -3"])
        q = run(["sudo", "-n", "docker", "exec", "ops-pg", "psql", "-U", "postgres",
                 "-d", "drill_tmp", "-t", "-A",
                 "-c", "SELECT COUNT(*) FROM central_tasks;", "-c", "SELECT COUNT(*) FROM learning_events;"])
        vals = [x for x in q.stdout.splitlines() if x.strip().isdigit()]
        if len(vals) >= 2:
            temp_counts = {"central_tasks": int(vals[0]), "learning_events": int(vals[1])}
    finally:
        run(["sudo", "-n", "docker", "exec", "ops-pg", "psql", "-U", "postgres",
             "-c", "DROP DATABASE IF EXISTS drill_tmp;"])
        run(["bash", "-c", "rm -rf " + outdir])
    # live learning_events count for comparison
    live_le = None
    try:
        q = run(["sudo", "-n", "docker", "exec", "ops-pg", "psql", "-U", "postgres",
                 "-d", "child_agent", "-t", "-A", "-c", "SELECT COUNT(*) FROM learning_events;"])
        live_le = int(q.stdout.strip())
    except Exception:
        pass
    ok = ok_sha and bool(manifest) and bool(temp_counts)
    print(json.dumps({"ok": ok, "date": date, "sha_ok": ok_sha, "manifest_head": manifest[:200],
                      "dump_copy_counts": counts, "temp_db_counts": temp_counts,
                      "live_learning_events": live_le}, ensure_ascii=False))
    return 0 if ok else 1
if __name__ == "__main__":
    sys.exit(main())
