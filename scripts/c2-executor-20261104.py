#!/usr/bin/env python3
"""C2 W1精简版 executor (2026-11-04, 令12实施).
只处理新任务, 白名单 payload.type in (llm_doc, script, patrol, timer) + code 且 workdir 非空, 不碰 QUARANTINED.
    code 类型: 枢纽不执行代码、不调模型, 只做 defer/路由: 校验 workdir 非空后释放租约、状态回 READY, 由 worker-1 经 queue_api/pull_once 执行 (l3-20261105-1a).
流程: READY -> DISPATCHING -> CLAIMED (lease fencing+1, LEASE_SECONDS from cq_common)
      -> RUNNING -> VERIFYING (artifact_sha/bytes 写入, verified_by 留空给 verifier)
script 类型只允许 echo/cat/ls/sha256sum 开头的安全命令, 超时 30s, 不联网, 不 sudo.
llm_doc 类型: 把 payload.content 写到 state/deliverables/<task_id>.md, 不调用模型.
DSN 只从 state/.cq-dsn 读, 无 DSN 则 fail closed, 不做任何事.
Cutover51 W3: 租约唯一定义 cq_common.LEASE_SECONDS=1200；认领写 lease_epoch；
后台 heartbeat 线程每 lease/3 续期一次（单条原子 UPDATE，deadline 封顶）；
续期返回 0 行 = 租约已丢，执行完不提交结果，任务回 RETRY_WAIT。
"""
import hashlib, json, os, subprocess, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from cq_common import LEASE_SECONDS, RENEW_INTERVAL_SECONDS
except Exception:
    LEASE_SECONDS, RENEW_INTERVAL_SECONDS = 1200, 400
DSN_FILE = "/home/ubuntu/ai-team/state/.cq-dsn"
DELIV_DIR = "/home/ubuntu/ai-team/state/deliverables"
ALLOWED_SCRIPT_PREFIXES = ("echo ", "cat ", "ls ", "sha256sum ")

GUARD_SCRIPT = "/home/ubuntu/ai-team/scripts/selfheal_guard-20261104.py"
GUARD_TYPE_MAP = {"llm_doc": "doc", "script": "script", "patrol": "patrol", "timer": "timer", "code": "script"}  # code->script: guard 无 code 类型, script 为最近似 auto 类型; 幂等/可重试/无副作用/category DENY 检查不变, guard 策略文件未动
def guard_check(ptype, payload, retry_count):
    # Order14 self-heal boundary, defense-in-depth over the executor allowlists.
    # Mapping: executor llm_doc -> guard doc. Category, when present in payload,
    # drives DENY for publish/money/delete/irreversible/prod_config_nonwhitelist/permission.
    # Without a category, type + retry_count govern; executor SQL already limits types.
    import json as _json, subprocess as _sp
    gtype = GUARD_TYPE_MAP.get(ptype, ptype)
    cat = payload.get("category", "") if isinstance(payload, dict) else ""
    req = {"type": gtype, "idempotent": True, "retryable": True, "external_side_effect": False, "retry_count": int(retry_count or 0), "category": cat}
    try:
        out = _sp.run(["python3", GUARD_SCRIPT], input=_json.dumps(req), capture_output=True, text=True, timeout=30)
        return _json.loads(out.stdout.strip() or "{}")
    except Exception as e:
        return {"decision": "DENY", "reason": "guard unavailable: " + str(e)[:120]}


def get_dsn():
    if not os.access(DSN_FILE, os.R_OK):
        return ""
    with open(DSN_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith("CQ_DSN="):
                v = line[len("CQ_DSN="):].strip().strip('"').strip("'")
                return v
    return ""

def _renewer(dsn, task_id, token, stop_event, lost_event):
    """Background lease renewal (cutover51 W3): every LEASE/3, one atomic
    UPDATE ... WHERE task_id AND lease_epoch AND expires_at>now(), capped by
    the task deadline. 0 rows = lease lost: set lost_event; the main flow
    must then NOT submit results. Runs on its own DB connection."""
    import psycopg
    while not stop_event.wait(RENEW_INTERVAL_SECONDS):
        try:
            with psycopg.connect(dsn) as conn:
                with conn.cursor() as cur:
                    _iv = str(LEASE_SECONDS)
                    cur.execute("""UPDATE central_leases SET last_heartbeat_at=now(),
                        expires_at=(SELECT LEAST(now()+interval '""" + _iv + """ seconds', COALESCE(t.deadline, now()+interval '""" + _iv + """ seconds')) FROM central_tasks t WHERE t.task_id=central_leases.task_id)
                        WHERE task_id=%s AND fencing_token=%s AND lease_epoch=%s AND status='ACTIVE' AND expires_at > now()""",
                        (task_id, token, token))
                    if cur.rowcount != 1:
                        lost_event.set()
                        print(f"c2-executor: lease LOST during execution {task_id} (renewal 0 rows)", flush=True)
                        return
                    conn.commit()
        except Exception as e:
            print(f"c2-executor: renewal error {task_id}: {e}", flush=True)


def main():
    dsn = get_dsn()
    if not dsn:
        print("c2-executor: no DSN, refusing")
        return 0
    import psycopg
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT task_id, payload, current_fencing_token, min_bytes, retry_count FROM central_tasks
                WHERE status = 'READY'
                  AND retry_count < COALESCE(max_retries, 3)
                  AND ((payload->>'type' IN ('llm_doc', 'script', 'patrol', 'timer')) OR (payload->>'type' = 'code' AND payload->>'workdir' IS NOT NULL AND payload->>'workdir' <> ''))
                  AND NOT EXISTS (SELECT 1 FROM central_leases l WHERE l.task_id = central_tasks.task_id AND l.status = 'ACTIVE')
                ORDER BY CASE lane WHEN 'P0' THEN 0 WHEN 'biz' THEN 1 WHEN 'infra' THEN 2 ELSE 9 END, created_at
                LIMIT 1 FOR UPDATE SKIP LOCKED""")
            row = cur.fetchone()
            if not row:
                print("c2-executor: no claimable task")
                return 0
            task_id, payload, token, min_bytes, retry_count = row
            new_token = token + 1
            cur.execute("UPDATE central_tasks SET status='DISPATCHING' WHERE task_id=%s", (task_id,))
            cur.execute("UPDATE central_tasks SET status='CLAIMED', current_fencing_token=%s WHERE task_id=%s", (new_token, task_id))
            cur.execute("INSERT INTO central_leases (task_id, worker_id, fencing_token, lease_epoch, expires_at) VALUES (%s, %s, %s, %s, now() + interval '" + str(LEASE_SECONDS) + " seconds')", (task_id, "c2-executor", new_token, new_token))
            conn.commit()
            print(f"c2-executor: claimed {task_id} fencing={new_token} lease_epoch={new_token} type={payload.get('type')}")
        # Cutover51 W3: background renewal thread (lease/3); loss => no submit.
        _stop = threading.Event(); _lost = threading.Event()
        _th = threading.Thread(target=_renewer, args=(dsn, task_id, new_token, _stop, _lost), daemon=True)
        _th.start()
        _guard = guard_check(payload.get("type"), payload, retry_count)
        with open("/home/ubuntu/ai-team/state/c2-guard.log", "a", encoding="utf-8") as _gl:
            _gl.write(f"{task_id} {payload.get('type')} {_guard.get('decision')} {_guard.get('reason')}\n")
        if _guard.get("decision") != "AUTO":
            with conn.cursor() as _gcur:
                _gcur.execute("UPDATE central_tasks SET status='QUARANTINED' WHERE task_id=%s", (task_id,))
                _gcur.execute("UPDATE central_leases SET status='RELEASED' WHERE task_id=%s AND status='ACTIVE'", (task_id,))
                # F1 2026-11-10 (failure_id 12): stamp the just-created
                # STATUS_CHANGE event row (diagnostic only).
                _gcur.execute(
                    "UPDATE central_events SET payload = jsonb_build_object('failure_class', %s, 'reason', %s)"
                    " WHERE event_id = (SELECT event_id FROM central_events"
                    " WHERE task_id=%s AND new_status='QUARANTINED' AND payload='{}'::jsonb"
                    " ORDER BY created_at DESC LIMIT 1)",
                    ("F-CONTRACT", ("c2-executor guard DENY: " + str(_guard.get("reason")))[:300], task_id))
                conn.commit()
            os.makedirs("/home/ubuntu/ai-team/state/c2-task-results", exist_ok=True)
            with open(f"/home/ubuntu/ai-team/state/c2-task-results/{task_id}-guard.json", "w", encoding="utf-8") as _gf:
                _gf.write(json.dumps({"task_id": str(task_id), "guard": _guard}, ensure_ascii=False))
            print(f"c2-executor: guard DENY {task_id}: {_guard.get('reason')}")
            return 0
        ptype = payload.get("type")
        artifact_bytes = None; artifact_sha = None; ok = True; err = ""; deferred = False
        try:
            if ptype == "llm_doc":
                content = payload.get("content", "")
                if not isinstance(content, str) or len(content.encode()) < 10:
                    raise ValueError("llm_doc content too short")
                os.makedirs(DELIV_DIR, exist_ok=True)
                path = os.path.join(DELIV_DIR, f"{task_id}.md")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                data = content.encode()
                artifact_bytes = len(data); artifact_sha = hashlib.sha256(data).hexdigest()
            elif ptype == "script":
                cmd = payload.get("command", "")
                if not any(cmd.startswith(p) for p in ALLOWED_SCRIPT_PREFIXES):
                    raise ValueError("script command not allowlisted")
                out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
                if out.returncode != 0:
                    raise ValueError(f"script exit {out.returncode}: {out.stderr[:200]}")
                data = out.stdout.encode()
                artifact_bytes = len(data); artifact_sha = hashlib.sha256(data).hexdigest()
            elif ptype == "patrol":
                # ORDER13-A: deterministic collect first (bounded 60s), then ONE
                # bounded gateway call to pool primary (60s). LLM failure does
                # NOT fail the task: deterministic file is already fresh.
                import time as _time, urllib.request as _url, json as _json
                det = subprocess.run(["python3", "/home/ubuntu/ai-team/scripts/patrol_deterministic.py"],
                                     capture_output=True, text=True, timeout=60)
                if det.returncode != 0:
                    raise ValueError(f"deterministic collect exit {det.returncode}: {det.stderr[:200]}")
                ppath = "/home/ubuntu/ai-team/ceo-seat/out/patrol-latest.md"
                if not os.path.exists(ppath):
                    raise ValueError("patrol-latest missing after deterministic collect")
                age = _time.time() - os.path.getmtime(ppath)
                if age > 300:
                    raise ValueError(f"patrol-latest stale age={age:.0f}s")
                try:
                    pool = _json.load(open("/home/ubuntu/ai-team/state/gateway-pool.json"))
                    primary = (pool.get("tier_a") or ["agnes-3.0-flash"])[0]
                except Exception:
                    primary = "agnes-3.0-flash"
                llm_ok = False
                _pllm_t0 = _time.monotonic(); _pllm_http = None; _pllm_txt = ""; _pllm_err = ""; _pllm_to = False; _pllm_rm = ""  # M9-TRACE-VERIFY
                try:
                    cfg = _json.load(open("/home/ubuntu/.config/opencode/opencode.json"))
                    tok = ""
                    for _pr in cfg.get("provider", {}).values():
                        _opt = _pr.get("options") or {}
                        if _opt.get("apiKey"):
                            tok = _opt["apiKey"]; break
                    body = _json.dumps({"model": primary, "messages": [{"role": "user", "content": "只回两个字：收到"}], "max_tokens": 10}).encode()
                    req = _url.Request("http://127.0.0.1:3001/v1/chat/completions", data=body,
                                       headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
                    with _url.urlopen(req, timeout=60) as _r:
                        _pllm_http = _r.status
                        _d = _json.loads(_r.read().decode() or "{}")
                    _txt = ((_d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                    _pllm_txt = _txt
                    _pllm_rm = _d.get("model", "") or ""
                    llm_ok = "收到" in _txt
                except Exception as _pllm_e:
                    _pllm_err = str(_pllm_e)[:200]
                    _pllm_http = getattr(_pllm_e, "code", None)
                    _pllm_to = "timed out" in str(_pllm_e).lower()
                    llm_ok = False
                # M5-3 trace (never breaks patrol): one line for the bounded gateway call.
                try:
                    import sys as _sys2
                    _sys2.path.insert(0, "/home/ubuntu/ai-team/scripts")
                    import trace_20261105 as _trace2
                    _trace2.emit(task_id=str(task_id), model_id=primary,
                                 latency_ms=int(round((_time.monotonic() - _pllm_t0) * 1000)),
                                 source="c2-executor-patrol", http_status=_pllm_http,
                                 returned_model=_pllm_rm, timeout_s=60, content=_pllm_txt,
                                 error=_pllm_err, timed_out=_pllm_to)
                except Exception:
                    pass
                with open("/home/ubuntu/ai-team/state/model_verdict.md", "w", encoding="utf-8") as _f:
                    _f.write(f"# model verdict\n- model: {primary}\n- llm_ok: {llm_ok}\n")
                data = open(ppath, "rb").read()
                artifact_bytes = len(data); artifact_sha = hashlib.sha256(data).hexdigest()
            elif ptype == "timer":
                # ORDER13-6: fixed-path timer scripts only, no arbitrary shell.
                # Task DONE means executed+verified; a timer check that finds a
                # problem reports it via morning anomaly, not via task RETRY.
                import time as _tt
                TIMER_SCRIPTS = {
                    "gateway_canary": ["python3", "/home/ubuntu/ai-team/scripts/gateway_canary-20261104.py", "--round", "timer"],
                    "audit_gateway": ["python3", "/home/ubuntu/ai-team/scripts/audit_gateway-20261104.py"],
                    "config_commit": ["python3", "/home/ubuntu/ai-team/scripts/config_commit-20261104.py"],
                    "backup_restore_drill": ["python3", "/home/ubuntu/ai-team/scripts/backup_restore_drill-20261104.py"],
                    "health_3q": ["python3", "/home/ubuntu/ai-team/scripts/health_3q-20261104.py"],
                    "storage_scan": ["python3", "/home/ubuntu/ai-team/scripts/storage_scan-20261104.py"],
                    "storage_confirm": ["python3", "/home/ubuntu/ai-team/scripts/storage_confirm-20261104.py"],
                    "storage_release": ["python3", "/home/ubuntu/ai-team/scripts/storage_release-20261104.py"],
                    "storage_audit": ["python3", "/home/ubuntu/ai-team/scripts/storage_audit-20261104.py"],
                    "canary_b": ["python3", "/home/ubuntu/ai-team/scripts/canary_b_tracker-20261104.py"],
                }
                tname = payload.get("timer")
                if tname not in TIMER_SCRIPTS:
                    raise ValueError(f"timer not allowlisted: {tname}")
                cmd = list(TIMER_SCRIPTS[tname])
                if tname == "gateway_canary":
                    try:
                        import json as _jj
                        pool = _jj.load(open("/home/ubuntu/ai-team/state/gateway-pool.json"))
                        models = (pool.get("tier_a") or []) + (pool.get("tier_b") or [])
                        import datetime as _dt
                        if _dt.datetime.now().weekday() == 6:
                            models += (pool.get("tier_x") or [])[:5]
                        cmd += ["--models", ",".join(models)]
                    except Exception:
                        cmd += ["--models", "glm-4-flash-cc,gemini-3.5-flash-lite"]
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                os.makedirs("/home/ubuntu/ai-team/state/c2-task-results", exist_ok=True)
                rpath = f"/home/ubuntu/ai-team/state/c2-task-results/{task_id}.txt"
                with open(rpath, "w", encoding="utf-8") as _f:
                    _f.write(f"TIMER_RC={out.returncode}\nTIMER={tname}\n")
                    _f.write(out.stdout[-4000:])
                    _f.write(out.stderr[-1000:])
                data = open(rpath, "rb").read()
                artifact_bytes = len(data); artifact_sha = hashlib.sha256(data).hexdigest()
            elif ptype == 'code':
                # l3-20261105-1a: hub executor does NOT execute code and does
                # NOT call any model. Defer/routing only: validate workdir,
                # release the lease, set status back to READY for worker-1
                # via queue_api/pull_once. No artifact is produced here.
                workdir = payload.get("workdir", "")
                if not isinstance(workdir, str) or not workdir.strip():
                    raise ValueError("code workdir empty")
                # Cutover51 W2.4: workdir is created ONLY at claim time
                # (/v1/claim). The old defer-time hub_exec mkdir is removed;
                # queue_api creates the workdir when worker-1 claims.
                deferred = True
                print(f"c2-executor: code deferred to worker-1 {task_id} workdir={workdir}")
            else:
                raise ValueError("unknown type")
        except Exception as e:
            ok = False; err = str(e)[:300]
            print(f"c2-executor: execute failed {task_id}: {err}")
        # Cutover51 W3 fence: stop renewal; if the lease was lost mid-run,
        # NEVER submit results — release and retry instead.
        _stop.set()
        _lease_lost = _lost.is_set()
        if _lease_lost:
            ok = False
            err = "lease lost during execution (renewal 0 rows); result NOT submitted"
            print(f"c2-executor: {err} {task_id}")
        with conn.cursor() as cur:
            if deferred:
                cur.execute("UPDATE central_tasks SET status='READY' WHERE task_id=%s AND status='CLAIMED'", (task_id,))
                cur.execute("UPDATE central_leases SET status='RELEASED' WHERE task_id=%s AND status='ACTIVE'", (task_id,))
                print(f"c2-executor: routed-to-worker-1 {task_id} status=READY retry_count unchanged")
            elif ok:
                cur.execute("UPDATE central_tasks SET status='RUNNING' WHERE task_id=%s AND status='CLAIMED'", (task_id,))
                cur.execute("UPDATE central_tasks SET status='VERIFYING', artifact_sha=%s, artifact_bytes=%s WHERE task_id=%s AND status='RUNNING'", (artifact_sha, artifact_bytes, task_id))
                print(f"c2-executor: submitted {task_id} sha={artifact_sha} bytes={artifact_bytes}")
            else:
                # F2 2026-11-10 (failure_id 11): retry>=max is terminal - QUARANTINED, no +1.
                cur.execute("UPDATE central_tasks SET status=CASE WHEN retry_count>=COALESCE(max_retries,3) THEN 'QUARANTINED' ELSE 'RETRY_WAIT' END, retry_count=CASE WHEN retry_count>=COALESCE(max_retries,3) THEN retry_count ELSE retry_count+1 END WHERE task_id=%s AND status IN ('CLAIMED','RUNNING') RETURNING status, retry_count", (task_id,))
                _fr = cur.fetchone()
                cur.execute("UPDATE central_leases SET status='RELEASED' WHERE task_id=%s AND status='ACTIVE'", (task_id,))
                # F1 2026-11-10 (failure_id 12): stamp the just-created
                # STATUS_CHANGE event row (diagnostic only; the audit
                # trigger writes payload '{}').
                _nst = _fr[0] if _fr else "RETRY_WAIT"
                _fclass = "F-TIMEOUT" if "lease lost" in err else "F-UNDIAG"
                cur.execute(
                    "UPDATE central_events SET payload = jsonb_build_object('failure_class', %s, 'reason', %s)"
                    " WHERE event_id = (SELECT event_id FROM central_events"
                    " WHERE task_id=%s AND new_status=%s AND payload='{}'::jsonb"
                    " ORDER BY created_at DESC LIMIT 1)",
                    (_fclass, ("c2-executor: " + err)[:300], task_id, _nst))
                print(f"c2-executor: fail {task_id} -> {_fr[0] if _fr else '?'} retry={_fr[1] if _fr else '?'}")
            conn.commit()
    return 0

if __name__ == "__main__":
    sys.exit(main())
