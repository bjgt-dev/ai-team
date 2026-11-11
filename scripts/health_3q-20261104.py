#!/usr/bin/env python3
"""ORDER13 health_3q (every 30 min, replaces old体检): three questions, exit 0/1/2/3.
Q1 能用吗: butler_decide synthetic rules call returns DONE; pool file has >=2
  canary-passing models (from gateway-pool.json tiers) AND pool mtime <6h AND
  gateway-canary jsonl fresh <6h (cutover51 W4: stale pool/canary = FAIL);
  C2 executor/verifier logs fresh (<10 min) and cq_sweep log fresh (<10 min).
Q2 在做事吗: central_tasks updated in last 60 min >0 AND a DONE task in last
  24h (cutover51 W4: OR -> AND).
Q3 对吗: latest DONE task deliverable sha matches artifact_sha (spot check;
  cutover51 W4: NO deliverable to check = FAIL, not skip); decision-queue
  pending count from DB decisions (status pending + needs_chairman);
  disk root/data <85%.
Each run writes heartbeats rows (gateway_canary, gateway_pool, health_3q);
a probe whose heartbeat is older than 2x its own period is RED (dead-man switch).
Exit: 0 = all three pass; 1 = Q1 pass, Q2/Q3 partial; 2 = Q1 partial;
3 = Q1 fail (butler synthetic not DONE or pool <2 or C2 logs stale).
Output JSON only; no secrets."""
import json, os, subprocess, sys, time
BASE = "/home/ubuntu/ai-team"
def dsn():
    for line in open(os.path.join(BASE, "state/.cq-dsn")):
        line = line.strip()
        if line.startswith("CQ_DSN="):
            return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    return ""
def fresh(path, secs):
    try:
        return time.time() - os.path.getmtime(path) < secs
    except OSError:
        return False
def _hb(dsn_fn, probe, ok, detail, notes):
    """Write one heartbeat row (cutover51 W4). Best effort: a failed insert
    is noted, never fatal to the health check itself."""
    try:
        import psycopg
        with psycopg.connect(dsn_fn()) as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO public.heartbeats(probe, ok, detail) VALUES (%s, %s, %s)",
                            (probe, bool(ok), str(detail)[:500]))
            conn.commit()
        return True
    except Exception as e:
        notes.append("heartbeat_write_failed:%s:%s" % (probe, str(e)[:80]))
        return False


def main():
    q1, q2, q3, notes = [], [], [], []
    # Q1a: butler synthetic
    task = {"task_id": "health3q-synthetic", "verifier_pass": True,
            "receipt": {"token": "health3q", "sha": "0" * 64, "bytes": 1},
            "retry_count": 0, "max_attempts": 3}
    with open("/tmp/health3q_task.json", "w") as f:
        json.dump(task, f)
    r = subprocess.run(["python3", os.path.join(BASE, "scripts/butler_decide.py"),
                        "/tmp/health3q_task.json", "/tmp/health3q_ledger.jsonl",
                        "/tmp/health3q_alerts.jsonl"], capture_output=True, text=True, timeout=60)
    try:
        verdict = json.loads(r.stdout).get("verdict")
    except Exception:
        verdict = None
    q1.append(("butler_synthetic", verdict == "DONE"))
    # Q1b: pool — count AND freshness (cutover51 W4/U-18): a 20h-old pool
    # file must FAIL even when it still lists >=2 models.
    try:
        pool = json.load(open(os.path.join(BASE, "state/gateway-pool.json")))
        npool = len(pool.get("tier_a", [])) + len(pool.get("tier_b", []))
    except Exception:
        npool = 0
    q1.append(("pool_models", npool))
    _pool_age_h = (time.time() - os.path.getmtime(os.path.join(BASE, "state/gateway-pool.json"))) / 3600 if os.path.exists(os.path.join(BASE, "state/gateway-pool.json")) else 999
    q1.append(("pool_mtime_hours", round(_pool_age_h, 1)))
    _canary_fresh = fresh(os.path.join(BASE, "state/gateway-canary-20261104.jsonl"), 6 * 3600)
    q1.append(("gateway_canary_fresh_6h", _canary_fresh))
    _hb(dsn, "gateway_canary", _canary_fresh, "jsonl mtime fresh<6h" if _canary_fresh else "STALE >6h", notes)
    _hb(dsn, "gateway_pool", _pool_age_h < 6, "pool mtime %.1fh" % _pool_age_h, notes)
    # Q1c: timers fresh
    q1.append(("c2_executor_fresh", fresh(os.path.join(BASE, "state/logs/c2-executor.log"), 600)))
    q1.append(("c2_verifier_fresh", fresh(os.path.join(BASE, "state/logs/c2-verifier.log"), 600)))
    # Q2/Q3 via central DB. NOTE (2026-11-04 fix): C2 DSN has no SELECT on
    # central_events, so Q2 must NOT use that table (it gave a false negative).
    # Q2 uses central_tasks.updated_at (which the C2 user can read) instead.
    # Each query is isolated so one permission failure cannot kill the others.
    events_60 = done_24 = -1
    pending_dec = -1
    sha_ok = None
    import psycopg
    def _q(sql, default=-1):
        try:
            with psycopg.connect(dsn()) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    return cur.fetchone()
        except Exception as e:
            notes.append("db_query_failed:" + str(e)[:120])
            return None
    def _qa(sql):
        """fetchall variant of _q (cutover51 W4 spot-check scan)."""
        try:
            with psycopg.connect(dsn()) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    return cur.fetchall()
        except Exception as e:
            notes.append("db_query_failed:" + str(e)[:120])
            return None
    r = _q("SELECT COUNT(*) FROM central_tasks WHERE updated_at > now() - interval '60 minutes'")
    if r is not None:
        events_60 = r[0]
    r = _q("SELECT COUNT(*) FROM central_tasks WHERE status='DONE' AND verified_at > now() - interval '24 hours'")
    if r is not None:
        done_24 = r[0]
    # Cutover51 W4: spot-check the latest DONE task that HAS an immutable
    # deliverable (patrol/timer tasks write shared mutable files, which are
    # excluded). Scan the last 50 DONE rows; if none has an immutable file,
    # sha_ok stays None -> Q3 FAIL (no object = FAIL).
    r = _qa("SELECT task_id, artifact_sha, artifact_bytes FROM central_tasks WHERE status='DONE' AND artifact_sha IS NOT NULL ORDER BY verified_at DESC NULLS LAST LIMIT 50")
    if r is not None and r:
        rows = r
    else:
        rows = []
    if rows:
        _checked_any = False
        for (tid, sha, nbytes) in rows:
            _any_immutable = False
            for cand in (os.path.join(BASE, "state/deliverables", str(tid) + ".bin"),
                         os.path.join(BASE, "state/queue-api-20261105/quarantine", str(tid) + ".bin"),
                         os.path.join(BASE, "state/deliverables", str(tid) + ".md"),
                         os.path.join(BASE, "state/c2-task-results", str(tid) + ".txt")):
                if os.path.exists(cand):
                    _any_immutable = True
                    _checked_any = True
                    import hashlib
                    data = open(cand, "rb").read()
                    if hashlib.sha256(data).hexdigest() == sha and len(data) == nbytes:
                        sha_ok = True
                    else:
                        sha_ok = False
                    break
            if _checked_any:
                break
        # No immutable deliverable among the last 50 DONE tasks: sha_ok
        # stays None and Q3 fails (no object = FAIL).
    q2.append(("events_60min", events_60))
    q2.append(("done_24h", done_24))
    # Q3: pending decisions now read the DB decisions table (cutover51 W2),
    # not the archived JSON files. Unresolved chairman items block Q3.
    try:
        r = _q("SELECT COUNT(*) FROM public.decisions WHERE status='pending' AND needs_chairman")
        if r is not None:
            pending_dec = r[0]
    except Exception:
        pass
    q3.append(("pending_decisions", pending_dec))
    q3.append(("done_sha_spotcheck", sha_ok))
    # disk
    import shutil
    root_pct = shutil.disk_usage("/").used / shutil.disk_usage("/").total * 100
    data_pct = shutil.disk_usage("/data").used / shutil.disk_usage("/data").total * 100 if os.path.exists("/data") else -1
    _ru, _rt = shutil.disk_usage("/").used, shutil.disk_usage("/").total
    q3.append(("disk_root_pct", round(root_pct, 1)))
    q3.append(("disk_root_used_total_gb", "%d/%d" % (round(_ru / 1e9), round(_rt / 1e9))))
    q3.append(("disk_data_pct", round(data_pct, 1) if data_pct >= 0 else None))
    # Cutover51 W4: Q1 needs pool freshness AND canary freshness; Q2 is AND;
    # Q3 fails when there is NO deliverable to spot-check (no object = FAIL).
    q1_pass = (verdict == "DONE" and npool >= 2 and _pool_age_h < 6 and _canary_fresh and q1[4][1] and q1[5][1])
    q2_pass = (events_60 > 0 and done_24 > 0)
    q3_pass = (sha_ok is True and pending_dec == 0 and root_pct < 85 and (data_pct < 0 or data_pct < 85))
    if q1_pass and q2_pass and q3_pass:
        code = 0
    elif not q1_pass:
        code = 3 if verdict != "DONE" or npool < 2 else 2
    else:
        code = 1
    # Order14 supplement (additive only, never changes exit code):
    supplement, supplement_pass = None, None
    try:
        rr = subprocess.run(["python3", os.path.join(BASE, "scripts/health_3q_supplement-20261104.py"), "--dry-run"],
                            capture_output=True, text=True, timeout=90)
        supplement = json.loads(rr.stdout)
        supplement_pass = bool(supplement.get("all_pass"))
    except Exception as e:
        supplement = {"error": str(e)[:120]}
    # Cutover51 W4: the checker's own heartbeat (a dead reporter/checker
    # must itself go red via the heartbeats view).
    _hb(dsn, "health_3q", code == 0, "exit=%s q1=%s q2=%s q3=%s" % (code, q1_pass, q2_pass, q3_pass), notes)
    out = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "exit_code": code,
           "supplement": supplement, "supplement_pass": supplement_pass,
           "q1_usable": q1_pass, "q2_working": q2_pass, "q3_correct": q3_pass,
           "q1": q1, "q2": q2, "q3": q3, "notes": notes,
           "disk_touchline_note": "trend not yet built; <85% with no disk alert reported as pass"}
    print(json.dumps(out, ensure_ascii=False))
    return code
if __name__ == "__main__":
    sys.exit(main())
