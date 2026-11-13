#!/usr/bin/env python3
"""wake_scheduler v2 — 2026-11-13 (12-worker rotation, replaces 20261105 single-machine).

Fixes the 2026-11-13 hub-executor drift: the v1 scheduler only ever woke
worker-1 (ALLOWLIST=["worker-1"], account-1 key only), so the other 11
machines sat suspended since 2026-11-04 while hub-local scripts did the
business execution. v2 wakes any of the 12 registered machines on demand.

Design (kept from v1, see its docstring for the decision order):
- pending = claimable READY count under the production executor whitelist
  (llm_doc/script/patrol/timer, plus code only with non-empty workdir),
  NOT the raw READY count.
- live = a machine in state "running" via the InstaBox API.
- "never wake without work", "never wake all at once", one short exec per
  wake/pull, hourly wake cap 6, concurrency cap 1 (one live worker at a
  time; a live worker gets a PULL, a suspended one gets a WAKE).
- Machine returns to suspended by the platform's own auto-suspend
  (1800 s idle); this file never pauses it.

v2 changes:
- MACHINES: all 12 registered machines across 4 InstaBox accounts
  (a1 worker-1..5, a2 worker-6..10, a3 agent-browser-llr0, a4
  agent-browser-bjgt2026qq). Source of truth is the worker registry
  (state/worker-registry.md); this list is a copy of it.
- READY: only machines with a verified pull_once entry point are woken
  or pulled. Read from state/queue-api-20261105/wake-ready.json
  ({"ready": ["worker-1", ...]}). A machine without pull_once is never
  woken (the wake would boot it and then fail to claim). Add a machine
  to READY only after its pull_once is verified on that machine.
- Rotation: wakes rotate across READY machines in registry order
  (state file wake-rotation.json stores the last-woken index), so one
  machine is never hammered alone. A live READY machine gets a PULL
  (same WAKE_COMMAND via exec, logged as action "pull").
- Validation (anti-drift): every run counts machine IDs in the worker
  registry and compares to len(MACHINES). On mismatch it writes
  state/outbox/wake-allowlist-alert.txt (and logs it). The allowlist
  must match the registry; a drift means someone changed one without
  the other.
- Modes: --dry-run (default), --tick (2-min cron), --verify-wake
  (one-shot probe wake; takes --machine NAME to target a specific
  READY machine, default = next in rotation).
- WAKE_COMMAND / PROBE_COMMAND: same as v1 (pull_once entry point;
  probe is read-only). A machine-specific pull_once path is used when
  present (currently identical on all machines: ~/pull_once/).

Crontab (unchanged path, file replaced in place; old version backed up
as wake_scheduler-20261105.py.bak-20261113-wakev2):
  */2 * * * * /home/ubuntu/ai-team/scripts/cron_flock.sh wake_scheduler python3 /home/ubuntu/ai-team/scripts/wake_scheduler-20261105.py --tick >> /home/ubuntu/ai-team/state/logs/wake-scheduler.log 2>&1
"""
import argparse, glob, json, os, re, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone

DSN_FILE = "/home/ubuntu/ai-team/state/.cq-dsn"
KEY_FILES = {
    "a1": "/home/ubuntu/ai-team/state/.instabox-api-key",
    "a2": "/home/ubuntu/ai-team/state/.instabox2-api-key",
    "a3": "/home/ubuntu/ai-team/state/.instabox3-api-key",
    "a4": "/home/ubuntu/ai-team/state/.instabox4-api-key",
}
REGISTRY_FILE = "/home/ubuntu/ai-team/state/worker-registry.md"
READY_FILE = "/home/ubuntu/ai-team/state/queue-api-20261105/wake-ready.json"
ROTATION_FILE = "/home/ubuntu/ai-team/state/queue-api-20261105/wake-rotation.json"
WAKE_LOG = "/home/ubuntu/ai-team/state/queue-api-20261105/wake-log.jsonl"
ALERT_FILE = "/home/ubuntu/ai-team/state/outbox/wake-allowlist-alert.txt"
# All 12 registered machines (copy of worker-registry.md 2026-11-13).
MACHINES = [
    {"name": "worker-1", "account": "a1", "machine_id": "m_agqv3bvofwxkfb2m4axoqjbju4"},
    {"name": "worker-2", "account": "a1", "machine_id": "m_agqv3c5yhvcfa36clq23hu336m"},
    {"name": "worker-3", "account": "a1", "machine_id": "m_agqv3n5d3ou3lloeoyv3zg5czq"},
    {"name": "worker-4", "account": "a1", "machine_id": "m_agqv3n5estnryd66fhihzavtzm"},
    {"name": "worker-5", "account": "a1", "machine_id": "m_agqv3n5fcdqga4mzdd65hcol5e"},
    {"name": "worker-6", "account": "a2", "machine_id": "m_agqv3oeaqad7z75wlewoy47eg4"},
    {"name": "worker-7", "account": "a2", "machine_id": "m_agqv3oeazcswsturdalymnbkgm"},
    {"name": "worker-8", "account": "a2", "machine_id": "m_agqv3oebcdlb3z6x75rvowduhe"},
    {"name": "worker-9", "account": "a2", "machine_id": "m_agqv3omteos5xmoapdrzosfywi"},
    {"name": "worker-10", "account": "a2", "machine_id": "m_agqv3omtnp3tf4q7hc4gsylyiu"},
    {"name": "agent-browser-llr0", "account": "a3", "machine_id": "m_agqyanqoo36otejy75f7ot552m"},
    {"name": "agent-browser-bjgt2026qq", "account": "a4", "machine_id": "m_agqya5nydkft2fx7v5ptit3je4"},
]
CONCURRENCY_CAP = 1
HOURLY_WAKE_CAP = 6
API_BASE = "https://api.instabox.sh/v1"
WAKE_COMMAND = "~/pull_once/tunnel.sh && python3 ~/pull_once/pull_once.py --once"
PROBE_COMMAND = (
    "hostname; uptime; date -u +%FT%TZ; "
    "ls -la ~/pull_once/ 2>&1; ls ~/code-agent-pilot/workdir/ 2>&1 | head -5"
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

def _api(key_file, method, path, body=None, timeout=30):
    key = open(key_file).read().strip()
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

def machine_states():
    """Return {machine_id: state} for all 12 machines (one API call per account)."""
    states = {}
    for account, key_file in KEY_FILES.items():
        status, payload = _api(key_file, "GET", "/machines", timeout=30)
        if status != 200:
            raise RuntimeError("instabox_list_failed account=%s status=%s" % (account, status))
        lst = payload if isinstance(payload, list) else payload.get("machines", payload.get("data", []))
        for m in lst:
            states[m.get("id")] = m.get("state")
    return states

def load_ready():
    """READY machines (verified pull_once). Default: worker-1 only (v1 behaviour)."""
    try:
        with open(READY_FILE) as f:
            data = json.load(f)
        ready = [n for n in (data.get("ready") or []) if isinstance(n, str)]
        if ready:
            return ready
    except Exception:
        pass
    return ["worker-1"]

def validate_allowlist():
    """Compare scheduler machine list vs worker registry. Returns (ok, detail)."""
    try:
        with open(REGISTRY_FILE) as f:
            text = f.read()
    except Exception as e:
        return False, "registry_unreadable: %s" % e
    reg_ids = set(re.findall(r"m_agq[a-z0-9]+", text))
    sched_ids = set(m["machine_id"] for m in MACHINES)
    if reg_ids == sched_ids:
        return True, "allowlist_matches_registry count=%d" % len(sched_ids)
    missing = sorted(sched_ids - reg_ids)
    extra = sorted(reg_ids - sched_ids)
    detail = "ALLOWLIST_DRIFT sched=%d reg=%d missing_in_registry=%s extra_in_registry=%s" % (
        len(sched_ids), len(reg_ids), missing, extra)
    try:
        os.makedirs(os.path.dirname(ALERT_FILE), exist_ok=True)
        with open(ALERT_FILE, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), detail))
    except Exception:
        pass
    return False, detail

def next_rotation_index(ready_names):
    """Round-robin index over READY machines, persisted in ROTATION_FILE."""
    try:
        with open(ROTATION_FILE) as f:
            idx = int(json.load(f).get("last_index", -1))
    except Exception:
        idx = -1
    idx = (idx + 1) % len(ready_names)
    try:
        os.makedirs(os.path.dirname(ROTATION_FILE), exist_ok=True)
        with open(ROTATION_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_index": idx, "last_machine": ready_names[idx]}, f)
    except Exception:
        pass
    return idx

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
    """CLAIMED/RUNNING rows: a worker is busy, do not start a second pull."""
    import psycopg
    with psycopg.connect(get_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM central_tasks WHERE status IN ('CLAIMED','RUNNING','VERIFYING') AND updated_at > now() - interval '20 minutes'")
            return cur.fetchone()[0]

def decide(pending, states, ready_names, wakes_hour):
    """Return (machine_dict_or_None, reason)."""
    if pending == 0:
        return None, "no_pending"
    try:
        if active_claims() > 0:
            return None, "worker_busy"
    except Exception:
        pass
    by_name = {m["name"]: m for m in MACHINES}
    ready = [by_name[n] for n in ready_names if n in by_name]
    live = [m for m in ready if states.get(m["machine_id"]) == "running"]
    if live:
        return live[0], "pull_live"
    if wakes_hour >= HOURLY_WAKE_CAP:
        return None, "hourly_cap"
    idx = next_rotation_index(ready_names)
    target = by_name[ready_names[idx]]
    return target, "wake"

def do_exec(machine, command, timeout=180):
    key_file = KEY_FILES[machine["account"]]
    return _api(key_file, "POST", "/machines/%s/exec" % machine["machine_id"],
                {"cmd": command}, timeout=timeout)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tick", action="store_true")
    ap.add_argument("--verify-wake", action="store_true")
    ap.add_argument("--machine", default=None, help="target a specific READY machine by name (verify-wake)")
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    valid, vdetail = validate_allowlist()
    ready_names = load_ready()
    try:
        pending = pending_claimable()
    except Exception as e:
        print(json.dumps({"error": str(e)[:200], "allowlist_valid": valid, "allowlist_detail": vdetail}))
        return 2
    try:
        states = machine_states()
    except Exception as e:
        print(json.dumps({"pending_claimable": pending, "error": str(e)[:200],
                          "allowlist_valid": valid, "allowlist_detail": vdetail}))
        return 2
    wh = wakes_last_hour()
    machine, reason = decide(pending, states, ready_names, wh)
    base = {"ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "ts_epoch": int(now.timestamp()),
            "pending_claimable": pending,
            "machine_states": {m["name"]: states.get(m["machine_id"]) for m in MACHINES},
            "ready": ready_names, "wakes_last_hour": wh,
            "decision": (machine or {}).get("name"), "reason": reason,
            "allowlist_valid": valid, "allowlist_detail": vdetail}
    if a.verify_wake:
        target = None
        if a.machine:
            # Probe is read-only: any registered machine may be probed
            # (READY only gates tick/wake, which runs pull_once).
            by_name = {m["name"]: m for m in MACHINES}
            if a.machine not in by_name:
                print(json.dumps(dict(base, action="refused_machine_unknown", target=a.machine), indent=2))
                return 2
            target = by_name[a.machine]
        else:
            target, reason = decide(pending, states, ready_names, wh)
            if target is None:
                print(json.dumps(dict(base, action="no_wake", reason=reason), indent=2))
                return 0
        if states.get(target["machine_id"]) == "running":
            print(json.dumps(dict(base, action="refused_worker_already_live", target=target["name"]), indent=2))
            return 0
        if wh >= HOURLY_WAKE_CAP:
            print(json.dumps(dict(base, action="refused_hourly_cap", target=target["name"]), indent=2))
            return 0
        _t0 = time.time()
        status, resp = do_exec(target, PROBE_COMMAND, timeout=180)
        wake_latency_s = round(time.time() - _t0, 1)
        try:
            states_after = machine_states()
            state_after = states_after.get(target["machine_id"], "unknown")
        except Exception:
            state_after = "unknown"
        entry = dict(base, target=target["name"], machine_id=target["machine_id"],
                     action="wake", command="probe", exec_status=status,
                     wake_latency_s=wake_latency_s, exec_output=str(resp)[:1500],
                     state_before=states.get(target["machine_id"]), state_after=state_after)
        log_wake(entry)
        print(json.dumps(entry, indent=2))
        return 0 if status == 200 else 3
    if a.dry_run or (not a.tick and not a.verify_wake):
        base["action"] = "NOT EXECUTED in dry-run"
        print(json.dumps(base, indent=2))
        return 0
    if a.tick:
        if machine is None:
            print(json.dumps(dict(base, action="no_wake"), indent=2))
            return 0
        if WAKE_COMMAND is None:
            print(json.dumps(dict(base, action="refused_pull_once_not_verified"), indent=2))
            return 0
        _t0 = time.time()
        status, resp = do_exec(machine, WAKE_COMMAND, timeout=180)
        wake_latency_s = round(time.time() - _t0, 1)
        try:
            states_after = machine_states()
            state_after = states_after.get(machine["machine_id"], "unknown")
        except Exception:
            state_after = "unknown"
        entry = dict(base, target=machine["name"], machine_id=machine["machine_id"],
                     action=("pull" if reason == "pull_live" else "wake"), command="pull_once",
                     exec_status=status, wake_latency_s=wake_latency_s,
                     exec_output=str(resp)[:1500],
                     state_before=states.get(machine["machine_id"]), state_after=state_after)
        log_wake(entry)
        print(json.dumps(entry, indent=2))
        return 0 if status == 200 else 3
    return 0

if __name__ == "__main__":
    sys.exit(main())
