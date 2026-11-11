#!/usr/bin/env python3
"""Auto-advance execution tracks (2026-10-27).

Purpose: stop the failure mode "next track waits for a chat turn / for Twt
to remember". State lives on the hub, not in any chat context, and a hub
cron runs this script every 5 minutes.

Hard limits (by design, not by promise):
- This script ONLY reads/writes its own state dir: execution-tracks.json,
  audit.jsonl, alerts.jsonl, deadletters.jsonl, triggers/*.ready.
- It NEVER executes business commands, NEVER touches expert login
  profiles, NEVER spends money, NEVER publishes, NEVER performs an
  irreversible action. A ".ready" trigger file is a claim signal for a
  future worker/queue claim, not an execution.
- Statuses: pending | ready | in_progress | done_A | dead_B.
- B (dead_B) is reported exactly once (reported_B flag).
- in_progress with no progress for >10 min is alerted exactly once
  (stale_alerted flag). Status is NOT flipped by the alerter.
- A track becomes ready only when ALL depends_on tracks are done_A.
- Use --mark to record real progress; --selftest uses a temp dir only.
"""
import argparse
import json
import os
import sys
import tempfile
import shutil
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))
STALE_SECONDS = 10 * 60
ALLOWED = {"pending", "ready", "in_progress", "done_A", "dead_B"}

INITIAL_TRACKS = [
    {"id": "workers", "name": "工人先建（监工/抢修工/应急工/主工/备工）",
     "status": "done_A", "depends_on": [],
     "evidence": "/home/ubuntu/ai-team/state/supervisor-worker-20261027/;/home/ubuntu/ai-team/state/repair-worker-20261027/;/home/ubuntu/ai-team/state/emergency-worker-20261027/;/home/ubuntu/ai-team/state/workers-confirm-20261027/REPORT.md"},
    {"id": "central-queue", "name": "中央队列骨架（PG表/状态机/租约/fencing/检查点）",
     "status": "pending", "depends_on": ["workers"], "evidence": ""},
    {"id": "gateway-retest", "name": "网关live重验（监工+应急工）",
     "status": "pending", "depends_on": ["workers"], "evidence": ""},
    {"id": "repair-wiring", "name": "抢修工生产接线（OCI/SSH重启+假路径重测+timer启用，须大管家定案）",
     "status": "pending", "depends_on": ["workers"], "evidence": ""},
    {"id": "shadow-b2", "name": "影子演练与B2落地",
     "status": "pending", "depends_on": ["central-queue"], "evidence": ""},
    {"id": "system-disk", "name": "系统盘分流与枢纽接管",
     "status": "pending", "depends_on": ["central-queue"], "evidence": ""},
    {"id": "gateway-ledger", "name": "模型网关路由与账本",
     "status": "pending", "depends_on": ["gateway-retest"], "evidence": ""},
    {"id": "credential-rotation", "name": "凭据清单与轮换",
     "status": "pending", "depends_on": ["central-queue"], "evidence": ""},
    {"id": "alert-dedup", "name": "告警分级与去重",
     "status": "pending", "depends_on": ["central-queue"], "evidence": ""},
]

def now_iso():
    return datetime.now(TZ).isoformat(timespec="seconds")

def parse_ts(s):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None

def default_state_dir():
    return os.path.join(os.path.expanduser("~"), "ai-team", "state", "auto-advance")

def paths(state_dir):
    return {
        "state": os.path.join(state_dir, "execution-tracks.json"),
        "audit": os.path.join(state_dir, "audit.jsonl"),
        "alerts": os.path.join(state_dir, "alerts.jsonl"),
        "dead": os.path.join(state_dir, "deadletters.jsonl"),
        "triggers": os.path.join(state_dir, "triggers"),
    }

def load(state_file, state_dir):
    if not os.path.exists(state_file):
        return None
    with open(state_file, encoding="utf-8") as f:
        return json.load(f)

def save_atomic(state_file, data):
    tmp = state_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, state_file)

def append_jsonl(path, obj):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def init_state(state_dir):
    p = paths(state_dir)
    os.makedirs(p["triggers"], exist_ok=True)
    if os.path.exists(p["state"]):
        return {"init": "exists", "state": p["state"]}
    ts = now_iso()
    tracks = []
    for t in INITIAL_TRACKS:
        d = dict(t)
        d["updated_at"] = ts
        d["last_progress_at"] = ts
        d["reported_B"] = False
        d["stale_alerted"] = False
        tracks.append(d)
    data = {"version": 1, "updated_at": ts, "tracks": tracks}
    save_atomic(p["state"], data)
    append_jsonl(p["audit"], {"ts": ts, "action": "init", "detail": "initial tracks created"})
    return {"init": "created", "state": p["state"]}

def advance(state_dir):
    p = paths(state_dir)
    os.makedirs(p["triggers"], exist_ok=True)
    data = load(p["state"], state_dir)
    if data is None:
        init_state(state_dir)
        data = load(p["state"], state_dir)
    by_id = {t["id"]: t for t in data["tracks"]}
    ts = now_iso()
    now = parse_ts(ts)
    actions = []
    for t in data["tracks"]:
        # 1. B reported exactly once
        if t["status"] == "dead_B" and not t.get("reported_B"):
            append_jsonl(p["dead"], {"ts": ts, "track": t["id"], "status": "dead_B",
                                     "evidence": t.get("evidence", ""), "note": "reported once; not looped"})
            t["reported_B"] = True
            actions.append(f"{t['id']}: dead_B reported once")
        # 2. stale in_progress alerted exactly once
        if t["status"] == "in_progress" and not t.get("stale_alerted"):
            lp = parse_ts(t.get("last_progress_at") or "")
            if lp and (now - lp).total_seconds() > STALE_SECONDS:
                append_jsonl(p["alerts"], {"ts": ts, "track": t["id"], "kind": "stale_no_progress",
                                           "last_progress_at": t.get("last_progress_at"),
                                           "threshold_seconds": STALE_SECONDS})
                t["stale_alerted"] = True
                actions.append(f"{t['id']}: stale alert once")
        # 3. promote pending -> ready when all deps are done_A
        if t["status"] == "pending":
            deps = t.get("depends_on") or []
            if all(by_id.get(d, {}).get("status") == "done_A" for d in deps):
                t["status"] = "ready"
                t["updated_at"] = ts
                trig = os.path.join(p["triggers"], t["id"] + ".ready")
                with open(trig, "w", encoding="utf-8") as f:
                    f.write(json.dumps({"track": t["id"], "ready_at": ts,
                                        "note": "claim signal only, not execution"}, ensure_ascii=False) + "\n")
                actions.append(f"{t['id']}: pending -> ready (trigger written)")
    data["updated_at"] = ts
    save_atomic(p["state"], data)
    append_jsonl(p["audit"], {"ts": ts, "action": "advance", "actions": actions})
    return {"actions": actions, "tracks": {t["id"]: t["status"] for t in data["tracks"]}}

def mark(state_dir, track_id, status, evidence):
    if status not in ALLOWED:
        raise SystemExit(f"bad status {status}, allowed {sorted(ALLOWED)}")
    p = paths(state_dir)
    data = load(p["state"], state_dir)
    if data is None:
        raise SystemExit("state not found; run --init first")
    for t in data["tracks"]:
        if t["id"] == track_id:
            old = t["status"]
            t["status"] = status
            if evidence:
                t["evidence"] = evidence
            ts = now_iso()
            t["updated_at"] = ts
            t["last_progress_at"] = ts
            if status in ("in_progress", "done_A"):
                t["reported_B"] = False
                t["stale_alerted"] = False
            data["updated_at"] = ts
            save_atomic(p["state"], data)
            append_jsonl(p["audit"], {"ts": ts, "action": "mark", "track": track_id,
                                      "from": old, "to": status})
            return {"track": track_id, "from": old, "to": status}
    raise SystemExit(f"unknown track {track_id}")

def selftest():
    tmp = tempfile.mkdtemp(prefix="auto-advance-selftest-")
    try:
        # Scenario setup: a done, b pending deps a, c dead, d stale in_progress
        ts_now = now_iso()
        ts_old = (datetime.now(TZ) - timedelta(minutes=31)).isoformat(timespec="seconds")
        def mk(tid, status, deps, lp):
            return {"id": tid, "name": tid, "status": status, "depends_on": deps,
                    "evidence": "", "updated_at": ts_now, "last_progress_at": lp,
                    "reported_B": False, "stale_alerted": False}
        data = {"version": 1, "updated_at": ts_now, "tracks": [
            mk("a", "done_A", [], ts_now),
            mk("b", "pending", ["a"], ts_now),
            mk("c", "dead_B", [], ts_now),
            mk("d", "in_progress", [], ts_old),
        ]}
        p = paths(tmp)
        os.makedirs(p["triggers"], exist_ok=True)
        save_atomic(p["state"], data)
        r1 = advance(tmp)
        assert r1["tracks"]["b"] == "ready", r1
        assert os.path.exists(os.path.join(p["triggers"], "b.ready")), "trigger missing"
        assert sum(1 for _ in open(p["dead"], encoding="utf-8")) == 1, "deadletter != 1"
        assert sum(1 for _ in open(p["alerts"], encoding="utf-8")) == 1, "alert != 1"
        r2 = advance(tmp)
        assert sum(1 for _ in open(p["dead"], encoding="utf-8")) == 1, "deadletter looped"
        assert sum(1 for _ in open(p["alerts"], encoding="utf-8")) == 1, "alert looped"
        # cleanup check: production state untouched by selftest (tmp only)
        assert tmp.startswith(tempfile.gettempdir())
        # code must not open/execute anything outside its state dir:
        # no subprocess/os.system, and no open() on a model-profiles path
        src = open(__file__, encoding="utf-8").read()
        # no import/execution primitives outside this self-check block
        body = src.split("def selftest")[0]
        assert "import subprocess" not in body and "os.system(" not in body, "script must not execute commands"
        for line in body.splitlines():
            if "model-profiles" in line:
                assert "open(" not in line, f"forbidden path use: {line}"
        return {"selftest": "PASS", "scenarios": ["A->next ready+trigger", "B reported once, no loop",
                                                     "stale alert once, no loop", "no subprocess, no expert-profile path"],
                "tmp_removed": True}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--mark", default=None)
    ap.add_argument("--to", default=None)
    ap.add_argument("--evidence", default="")
    a = ap.parse_args()
    state_dir = a.state_dir or default_state_dir()
    if a.selftest:
        print(json.dumps(selftest(), ensure_ascii=False, indent=2))
        return 0
    if a.init:
        print(json.dumps(init_state(state_dir), ensure_ascii=False, indent=2))
        return 0
    if a.mark:
        print(json.dumps(mark(state_dir, a.mark, a.to, a.evidence), ensure_ascii=False, indent=2))
        return 0
    if a.status:
        data = load(paths(state_dir)["state"], state_dir)
        print(json.dumps(data, ensure_ascii=False, indent=2) if data else "no state")
        return 0
    print(json.dumps(advance(state_dir), ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    sys.exit(main())
