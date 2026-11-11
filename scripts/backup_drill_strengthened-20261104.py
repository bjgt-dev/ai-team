#!/usr/bin/env python3
"""Order14 backup strengthening drill (real drill, temp resources only).
Steps, in order, all read-only except temp DB + /tmp:
1. Latest backup date from backups-repo/backups (actual list, not design).
   Retention report: days present, first/last date, missing dates in range.
2. SHA256SUMS check via restore_assets.sh (decrypts to /tmp/restore-<date>).
3. MANIFEST full-file sha comparison: for every file listed in
   payload/MANIFEST.txt with a sha, recompute sha256 and compare;
   report total, matched, mismatched (with names, never file contents).
4. Temp PG container load: pg_restore-style load of pg_all.sql.gz into a
   throwaway drill_tmp database (filtered, no live DB touched), then run
   5 business queries: central_tasks total, DONE count, READY count,
   QUARANTINED count, latest DONE verified_at; plus flags count and
   central_events count for reconciliation. Compare temp counts to live
   counts where the table exists in both.
5. Key-file spot checks: opencode.json parses as JSON (provider count
   only, no values printed); crontab parses (each non-comment line has
   5 time fields or an @ schedule).
6. Cleanup: drop drill_tmp, remove /tmp/restore-<date>.
Exit 0 only if SHA ok, MANIFEST mismatches == 0, temp load ok, and
key files parse. Retention days present is REPORTED, not a pass/fail
gate (actual list may be <14; do not claim 14 if the list is shorter).
"""
import hashlib, json, os, re, subprocess, sys, time, glob
REPO = "/home/ubuntu/backups-repo"

def run(cmd, timeout=600):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

def main():
    dates = sorted([d for d in os.listdir(os.path.join(REPO, "backups"))
                    if re.match(r"^\d{4}-\d{2}-\d{2}$", d)])
    if not dates:
        print(json.dumps({"ok": False, "error": "no backup dates"})); return 1
    date = dates[-1]
    # retention actual
    from datetime import datetime, timedelta
    d0, d1 = datetime.strptime(dates[0], "%Y-%m-%d"), datetime.strptime(dates[-1], "%Y-%m-%d")
    span = set()
    cur = d0
    while cur <= d1:
        span.add(cur.strftime("%Y-%m-%d")); cur += timedelta(days=1)
    missing = sorted(span - set(dates))
    retention = {"days_present": len(dates), "first": dates[0], "last": dates[-1], "missing_in_range": missing}
    # decrypt via existing restore script (prints MANIFEST, does not touch live)
    r = run(["bash", "/home/ubuntu/ai-team/scripts/restore_assets.sh", date])
    sha_ok = "OK" in r.stdout
    outdir = "/tmp/restore-" + date
    # MANIFEST full comparison
    man_total, man_ok, mism = 0, 0, []
    try:
        man = open(os.path.join(outdir, "payload/MANIFEST.txt")).read()
        for line in man.splitlines():
            parts = line.split()
            if len(parts) >= 2 and re.match(r"^[0-9a-f]{64}$", parts[0]):
                sha, rel = parts[0], parts[1]
                man_total += 1
                p = os.path.join(outdir, "payload", rel)
                if os.path.exists(p):
                    h = hashlib.sha256(open(p, "rb").read()).hexdigest()
                    if h == sha:
                        man_ok += 1
                    else:
                        mism.append(rel)
                else:
                    mism.append(rel + " (missing)")
    except Exception as e:
        mism.append("manifest_read_error:" + str(e)[:120])
    manifest = {"total_listed": man_total, "matched": man_ok, "mismatched": mism}
    # temp PG load + 5 business queries
    temp_counts, live_counts = {}, {}
    try:
        run(["sudo", "-n", "docker", "exec", "ops-pg", "psql", "-U", "postgres",
             "-c", "DROP DATABASE IF EXISTS drill_tmp;", "-c", "CREATE DATABASE drill_tmp;"])
        dump = os.path.join(outdir, "payload/pg_all.sql.gz")
        load = run(["bash", "-c", "zcat " + dump + " | grep -v '^\\\\connect' | grep -v 'CREATE DATABASE' | sudo -n docker exec -i ops-pg psql -U postgres -d drill_tmp 2>&1 | tail -3"])
        queries = {
            "central_tasks_total": "SELECT COUNT(*) FROM central_tasks;",
            "done_count": "SELECT COUNT(*) FROM central_tasks WHERE status='DONE';",
            "ready_count": "SELECT COUNT(*) FROM central_tasks WHERE status='READY';",
            "quarantined_count": "SELECT COUNT(*) FROM central_tasks WHERE status='QUARANTINED';",
            "latest_done": "SELECT MAX(verified_at)::text FROM central_tasks WHERE status='DONE';",
            "flags_count": "SELECT COUNT(*) FROM flags;",
            "events_count": "SELECT COUNT(*) FROM central_events;",
        }
        for k, q in queries.items():
            rr = run(["sudo", "-n", "docker", "exec", "ops-pg", "psql", "-U", "postgres", "-d", "drill_tmp", "-t", "-A", "-c", q])
            temp_counts[k] = rr.stdout.strip()
        for k, q in queries.items():
            rr = run(["sudo", "-n", "docker", "exec", "ops-pg", "psql", "-U", "postgres", "-d", "central_queue", "-t", "-A", "-c", q])
            live_counts[k] = rr.stdout.strip()
    except Exception as e:
        temp_counts["error"] = str(e)[:200]
    finally:
        run(["sudo", "-n", "docker", "exec", "ops-pg", "psql", "-U", "postgres", "-c", "DROP DATABASE IF EXISTS drill_tmp;"])
        run(["bash", "-c", "rm -rf " + outdir])
    # key files
    try:
        cfg = json.load(open("/home/ubuntu/.config/opencode/opencode.json"))
        op_ok = True; providers = len(cfg.get("provider") or {})
    except Exception:
        op_ok = False; providers = 0
    try:
        cr = run(["crontab", "-l"])
        lines = [l for l in cr.stdout.splitlines() if l.strip() and not l.strip().startswith("#")]
        cron_ok = all((len(l.split()) >= 6 or l.lstrip().startswith("@")) for l in lines)
        cron_lines = len(lines)
    except Exception:
        cron_ok = False; cron_lines = 0
    ok = sha_ok and not mism and bool(temp_counts.get("central_tasks_total")) and op_ok and cron_ok
    print(json.dumps({"ok": ok, "date": date, "sha_ok": sha_ok, "retention_actual": retention,
                      "manifest_full_sha": manifest, "temp_db_counts": temp_counts,
                      "live_counts": live_counts,
                      "key_files": {"opencode_parses": op_ok, "provider_count": providers,
                                    "crontab_parses": cron_ok, "crontab_lines": cron_lines}},
                     ensure_ascii=False, indent=2))
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
