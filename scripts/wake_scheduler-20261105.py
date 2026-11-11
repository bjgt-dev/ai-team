#!/usr/bin/env python3
"""wake_scheduler — PRODUCTION install 2026-11-05 (task 3 of 4, single-machine stage).
Adapted from Window B draft C (state/window-b-20261104/wake_scheduler_draft.py).
Reused from draft: decision order (pending -> live -> concurrency -> hourly
cap), concurrency cap, hourly cap 6, live window, "never wake without work",
"never wake all at once", "never manually suspend/pause afterwards", one
short InstaBox exec per wake (exec auto-wakes an idle-paused machine; a
machine in max-run cooldown answers 409 machine_cooldown).

Adaptations for production (install review decisions, all recorded in
state/queue-api-20261105/EVIDENCE-20261105.md):
- ALLOWLIST IS ["worker-1"] ONLY. The draft's 12-worker rotation is NOT
  installed. No other worker is ever woken by this file; the InstaBox key
  used is account 1 only (state/.instabox-api-key) and the machine id is
  pinned (m_agqv3bvofwxkfb2m4axoqjbju4). Other accounts' keys are never read.
- pending = claimable READY count under the production executor whitelist
  (llm_doc/script/patrol/timer, plus code only with non-empty workdir),
  NOT the raw READY count. The single READY task today is the OCI code
  task with NULL workdir, so pending = 0 and no wake is decided.
- live = worker-1 machine state == "running" via the InstaBox API (there is
  no wb.workers table in production; the draft's last_seen check cannot run).
- wakes/hour is counted from the append-only wake log
  (state/queue-api-20261105/wake-log.jsonl), not from a workers table.
- Wake log: every wake (and every refused verify-wake) is appended as one
  JSON line with task/worker/timestamp (+ machine state before/after).
- Modes:
    --dry-run   (default) print the decision, make no wake, write nothing.
    --tick      the 2-minute C2 timer mode: evaluate; if the decision is to
                wake, run WAKE_COMMAND once on worker-1 and log it.
    --verify-wake  one-shot verification wake with PROBE_COMMAND (read-only
                probe on the worker: hostname, pilot dir listing, agent
                interface). Used once by task 3 to prove the wake path
                without claiming any task. Logs task=null explicitly.
- WAKE_COMMAND (production pull_once path) is set only after the worker-side
  pull_once entry point is verified on worker-1 (see EVIDENCE-20261105.md).
  Until then it is None and --tick refuses with
  "pull_once_not_verified". The cron timer is NOT wired until then.
- PROBE_COMMAND never starts a pull loop, never claims, never writes on
  the worker beyond stdout. The machine returns to suspended by the
  platform's own auto-suspend (1800 s idle); this file never pauses it.
- InstaBox exec timeout 180 s (wake + boot + probe). Cooldown 409 is
  recorded in the wake log and ends the tick without retry.
Crontab line for install review (NOT installed until WAKE_COMMAND verified):
  */2 * * * * /home/ubuntu/ai-team/scripts/cron_flock.sh wake_scheduler python3 /home/ubuntu/ai-team/scripts/wake_scheduler-20261105.py --tick >> /home/ubuntu/ai-team/state/logs/wake-scheduler.log 2>&1
"""
import argparse, glob, json, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone

DSN_FILE = "/home/ubuntu/ai-team/state/.cq-dsn"
KEY_FILE = "/home/ubuntu/ai-team/state/.instabox-api-key"
WAKE_LOG = "/home/ubuntu/ai-team/state/queue-api-20261105/wake-log.jsonl"
ALLOWLIST = ["worker-1"]
WORKER1_MACHINE_ID = "m_agqv3bvofwxkfb2m4axoqjbju4"
CONCURRENCY_CAP = 1
HOURLY_WAKE_CAP = 6
API_BASE = "https://api.instabox.sh/v1"
# Production pull_once entry point on worker-1. None until verified on the
# worker (see EVIDENCE-20261105.md); --tick refuses while None.
WAKE_COMMAND = "~/pull_once/tunnel.sh && python3 ~/pull_once/pull_once.py --once"  # 2026-11-05 pull_once verified: one claim cycle DONE via queue_api (see state/pull-once-20261105/EVIDENCE-20261105.md)
PROBE_COMMAND = (
    "hostname; uptime; date -u +%FT%TZ; "
    "ls -la ~/code-agent-pilot/ 2>&1; ls ~/code-agent-pilot/workdir/ 2>&1; "
    "ls -la ~/pull-loop-pilot/ 2>&1; "
    "grep -n -E 'once|pull|queue_api|argparse|add_argument' ~/code-agent-pilot/agent.py 2>&1 | head -30; "
    "sed -n '1,40p' ~/code-agent-pilot/agent.py 2>&1 | head -45"
)

def log_wake(entry):
    os.makedirs(os.path.dirname(WAKE_LOG), exist_ok=True)
    with open(WAKE_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def get_dsn():
    try:
        with open(DSN_FILE) as f:
            for line in f:
                line = line.strip()
                if line.startswith("CQ_DSN="):
                    return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""

def pending_claimable():
    import psycopg
    dsn = get_dsn()
    if not dsn:
        raise RuntimeError("no_dsn_refusing")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT count(*) FROM central_tasks WHERE status='READY'
                AND ((payload->>'type' IN ('llm_doc', 'script', 'patrol', 'timer'))
                     OR (payload->>'type' = 'code' AND payload->>'workdir' IS NOT NULL
                         AND payload->>'workdir' <> ''))
                AND NOT EXISTS (SELECT 1 FROM central_leases l WHERE l.task_id = central_tasks.task_id AND l.status = 'ACTIVE')""")
            return cur.fetchone()[0]

def _api(method, path, body=None, timeout=30):
    key = open(KEY_FILE).read().strip()
    req = urllib.request.Request(API_BASE + path, method=method,
                                 headers={"Authorization": "Bearer " + key,
                                          "Content-Type": "application/json"})
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {"error": "http_error_no_body"}
    except Exception as e:
        return 0, {"error": type(e).__name__, "detail": str(e)[:200]}

def machine_state():
    status, payload = _api("GET", "/machines", timeout=30)
    if status != 200:
        raise RuntimeError("instabox_list_failed status=%s" % status)
    lst = payload if isinstance(payload, list) else payload.get("machines", payload.get("data", []))
    for m in lst:
        if m.get("id") == WORKER1_MACHINE_ID:
            return m.get("state"), m
    raise RuntimeError("worker1_machine_not_found")

def wakes_last_hour():
    if not os.path.exists(WAKE_LOG):
        return 0
    cutoff = time.time() - 3600
    n = 0
    with open(WAKE_LOG, encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("action") != "wake":
                continue
            ts = e.get("ts_epoch", 0)
            if ts and ts >= cutoff:
                n += 1
    return n

def active_claims():
    """CLAIMED/RUNNING rows: worker is busy, do not start a second pull."""
    import psycopg
    with psycopg.connect(get_dsn()) as conn:
        with conn.cursor() as cur:
            # Only RECENT active claims count as busy: a CLAIMED row whose
            # updated_at is >20 min old is a stale lease (stall-scan already
            # flags it red at 60 min); counting it would block every pull
            # forever. Lease is 600s + heartbeat margin.
            cur.execute("SELECT count(*) FROM central_tasks WHERE status IN ('CLAIMED','RUNNING','VERIFYING') AND updated_at > now() - interval '20 minutes'")
            return cur.fetchone()[0]


def decide(pending, live_running, wakes_hour):
    # Formal pipeline fix 2026-11-06: the old code returned
    # "worker_already_live" and did nothing while the machine was live.
    # That assumed a live worker pulls on its own — it does not: pull_once
    # is single-cycle, there is no worker-side loop, and WAKE_COMMAND only
    # ever fired on wake-from-suspended. So READY tasks accumulated while
    # the machine sat live (canary-20261106b: pending 5, live, no claim
    # for 22 min). Fix: a live worker with pending work gets a PULL —
    # the same WAKE_COMMAND via InstaBox exec, logged as action "pull",
    # not counted against the hourly WAKE cap (wakes_last_hour counts
    # action=="wake" only). A suspended worker still gets a WAKE.
    # A busy worker (active claim) gets neither.
    if pending == 0:
        return None, "no_pending"
    try:
        if active_claims() > 0:
            return None, "worker_busy"
    except Exception:
        pass
    if live_running:
        return "worker-1", "pull_live"
    if wakes_hour >= HOURLY_WAKE_CAP:
        return None, "hourly_cap"
    return "worker-1", "wake"

def do_exec(command, timeout=180):
    # Proven shape (check_worker1b.py): {"cmd": "<shell string>"}.
    # {"command": ...} is rejected with 400 cmd-is-required.
    return _api("POST", "/machines/%s/exec" % WORKER1_MACHINE_ID,
                {"cmd": command}, timeout=timeout)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tick", action="store_true")
    ap.add_argument("--verify-wake", action="store_true")
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    try:
        pending = pending_claimable()
    except Exception as e:
        print(json.dumps({"error": str(e)[:200]}))
        return 2
    try:
        state, _m = machine_state()
    except Exception as e:
        print(json.dumps({"pending_claimable": pending, "error": str(e)[:200]}))
        return 2
    live_running = (state == "running")
    wh = wakes_last_hour()
    worker, reason = decide(pending, live_running, wh)
    base = {"ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "ts_epoch": int(now.timestamp()),
            "worker": "worker-1", "machine_id": WORKER1_MACHINE_ID,
            "pending_claimable": pending, "machine_state": state,
            "live_running": live_running, "wakes_last_hour": wh,
            "decision": worker, "reason": reason}
    if a.dry_run or (not a.tick and not a.verify_wake):
        base["action"] = "NOT EXECUTED in dry-run"
        print(json.dumps(base, indent=2))
        return 0
    if a.verify_wake:
        if live_running:
            base["action"] = "refused_worker_already_live"
            log_wake(dict(base, task=None))
            print(json.dumps(base, indent=2))
            return 0
        if wh >= HOURLY_WAKE_CAP:
            base["action"] = "refused_hourly_cap"
            log_wake(dict(base, task=None))
            print(json.dumps(base, indent=2))
            return 0
        _t0 = time.time()
        status, resp = do_exec(PROBE_COMMAND, timeout=180)
        wake_latency_s = round(time.time() - _t0, 1)  # exec round-trip incl. wake+boot+probe
        try:
            state_after, _m2 = machine_state()
        except Exception:
            state_after = "unknown"
        entry = dict(base, task=None, action="wake", command="probe",
                     exec_status=status, wake_latency_s=wake_latency_s,
                     exec_output=str(resp)[:1500],
                     state_before=state, state_after=state_after)
        log_wake(entry)
        print(json.dumps(entry, indent=2))
        return 0 if status == 200 else 3
    if a.tick:
        if worker is None:
            print(json.dumps(dict(base, action="no_wake"), indent=2))
            return 0
        if WAKE_COMMAND is None:
            print(json.dumps(dict(base, action="refused_pull_once_not_verified"), indent=2))
            return 0
        _t0 = time.time()
        status, resp = do_exec(WAKE_COMMAND, timeout=180)
        wake_latency_s = round(time.time() - _t0, 1)  # exec round-trip incl. wake+boot+pull_once
        try:
            state_after, _m2 = machine_state()
        except Exception:
            state_after = "unknown"
        entry = dict(base, task=None, action=("pull" if reason == "pull_live" else "wake"), command="pull_once",
                     exec_status=status, wake_latency_s=wake_latency_s,
                     exec_output=str(resp)[:1500],
                     state_before=state, state_after=state_after)
        log_wake(entry)
        print(json.dumps(entry, indent=2))
        return 0 if status == 200 else 3
    return 0

if __name__ == "__main__":
    sys.exit(main())
