#!/usr/bin/env python3
"""Backup watchdog (备工触发器) — 2026-10-30 终极改造第二项.

Fires ONLY on executor-level failure, never on quality REJECTs:
  (a) route empty sustained: model-health route simple_query == 0 for >=30 min
  (b) executor streak: last 3 distinct executor runs all agent_exit != 0
On trigger: probe-first self-heal (model-health probe). If route restored,
stop (normal executor resumes). Else run minimal backup execution for the
oldest stuck task via hub_chat.py (gateway, explicit model, never the
foreman-prime default), write deliverable + receipt, notify. If backup also
fails, escalate to decision queue and stop (no loop).
Guards: flock (one watchdog), per-task 24h cooldown, max 1 task per run,
never touches InstaBox wake, never runs on quality REJECTs.
Usage: python3 backup-watchdog.py [--dry-run]
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BASE = Path("/home/ubuntu/ai-team")
STATE = BASE / "state"
WORK = BASE / "ceo-seat" / "work"
OUT = BASE / "ceo-seat" / "out"
SCRIPTS = BASE / "scripts"
EXEC_LOG = STATE / "agent-executor-main-worker.log"
MH = SCRIPTS / "model-health.py"
HUB_CHAT = SCRIPTS / "hub_chat.py"
NOTIFY = WORK / "notify.py"
WATCH_STATE = STATE / "backup-watchdog-state.json"
LOCK = Path("/tmp/backup-watchdog.lock")
MODELS_TRY = ["glm-4-flash-cc", "deepseek-chat"]
ROUTE_EMPTY_SUSTAIN_S = 1800
COOLDOWN_S = 86400


def load_state():
    try:
        return json.loads(WATCH_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"first_empty_at": None, "triggered": {}}


def save_state(s):
    WATCH_STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def route_count():
    """Authoritative route size via model-health route (fast filter, no probe)."""
    try:
        r = subprocess.run([sys.executable, str(MH), "route", "--capability", "simple_query"],
                           capture_output=True, text=True, timeout=60)
        out = r.stdout.strip()
        if out.startswith("{"):
            try:
                return len(json.loads(out).get("models", []))
            except Exception:
                pass
        models = [ln.strip() for ln in out.splitlines() if ln.strip()]
        return len(models)
    except Exception:
        return -1


def exec_streak():
    """Last 3 distinct executor runs; True if all agent_exit != 0."""
    try:
        lines = EXEC_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return False
    seen = []
    for ln in reversed(lines):
        m = re.search(r"done (\S+): EXECUTED .*agent_exit=(\d+)", ln)
        if not m:
            continue
        tid, code = m.group(1), int(m.group(2))
        if tid in [t for t, _ in seen]:
            continue
        seen.append((tid, code))
        if len(seen) >= 3:
            break
    return len(seen) >= 3 and all(c != 0 for _, c in seen)


def oldest_stuck():
    """Oldest pending contract, else oldest five-step PENDING task."""
    pend = BASE / "contracts" / "pending"
    cands = []
    if pend.exists():
        for f in pend.rglob("*.json"):
            try:
                cands.append((f.stat().st_mtime, f.stem))
            except Exception:
                pass
    if cands:
        cands.sort()
        return cands[0][1]
    for f in sorted(WORK.glob("five-step-*.md")):
        try:
            txt = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "step5-verify PENDING" in txt or "PENDING" in txt:
            tid = f.stem[len("five-step-"):]
            if "-fb" not in tid:
                return tid
    return None


def read_contract(tid):
    for d in (BASE / "contracts" / "pending", BASE / "contracts" / "claimed",
              BASE / "contracts" / "done"):
        for f in d.rglob(tid + ".json"):
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                pass
    return None


def notify(type_, task_id, summary):
    try:
        subprocess.run([sys.executable, str(NOTIFY), "push", "--type", type_,
                        "--task-id", task_id, "--summary", summary],
                       capture_output=True, text=True, timeout=60)
    except Exception:
        pass


def backup_run(objective, path):
    """Core backup execution: hub_chat -> deliverable + receipt. Testable."""
    import hashlib
    prompt = objective + "\n\n要求：只输出完整正文，不要状态汇报、不要提纲、不要复制任务书。"
    out_text = None
    used = None
    for model in MODELS_TRY:
        try:
            r = subprocess.run([sys.executable, str(HUB_CHAT), model],
                               input=prompt, capture_output=True, text=True, timeout=250)
            if r.returncode == 0 and r.stdout.strip():
                out_text, used = r.stdout, model
                break
        except Exception:
            continue
    if not out_text:
        return {"ok": False, "reason": "all_models_failed"}
    pp = Path(path)
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_text(out_text, encoding="utf-8")
    sha = hashlib.sha256(out_text.encode("utf-8")).hexdigest()
    return {"ok": True, "model": used, "path": path, "sha256": sha}


def backup_execute(tid, dry_run):
    """Minimal backup execution: contract -> hub_chat -> deliverable + receipt."""
    c = read_contract(tid)
    if not c:
        return {"ok": False, "reason": "no_contract"}
    obj = c.get("objective", "")
    path = c.get("output_file") or c.get("deliverable_path") or ""
    if not obj or not path:
        return {"ok": False, "reason": "contract_incomplete"}
    if dry_run:
        return {"ok": True, "dry_run": True, "task_id": tid, "path": path}
    res = backup_run(obj, path)
    if not res.get("ok"):
        return res
    receipt = {"tool": "backup-watchdog", "status": "BACKUP_EXECUTED", "task_id": tid,
               "model": res["model"], "deliverable": path, "sha256": res["sha256"],
               "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    (STATE / ("backup-receipt-" + tid + ".json")).write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "task_id": tid, "model": res["model"], "path": path, "sha256": res["sha256"]}


def main():
    dry_run = "--dry-run" in sys.argv
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        print(json.dumps({"status": "BUSY", "reason": "watchdog_lock_held"}))
        return 2
    try:
        st = load_state()
        now = time.time()
        rc = route_count()
        trig = None
        if rc == 0:
            if not st.get("first_empty_at"):
                st["first_empty_at"] = now
                save_state(st)
            elif now - st["first_empty_at"] >= ROUTE_EMPTY_S:
                trig = "route_empty_30min"
        else:
            if st.get("first_empty_at"):
                st["first_empty_at"] = None
                save_state(st)
        if not trig and exec_streak():
            trig = "executor_fail_streak_3"
        if not trig:
            print(json.dumps({"status": "OK", "trigger": None, "route": rc}))
            return 0
        tid = oldest_stuck()
        if not tid:
            print(json.dumps({"status": "TRIGGERED_NO_TASK", "trigger": trig, "route": rc}))
            return 0
        last = st.get("triggered", {}).get(tid, 0)
        if now - last < COOLDOWN_S:
            print(json.dumps({"status": "COOLDOWN_SKIP", "trigger": trig, "task_id": tid}))
            return 0
        # Probe-first self-heal: a fresh probe often restores the route.
        try:
            subprocess.run([sys.executable, str(MH), "probe"], capture_output=True,
                           text=True, timeout=600)
        except Exception:
            pass
        rc2 = route_count()
        if rc2 > 0 and trig == "route_empty_30min":
            st["first_empty_at"] = None
            save_state(st)
            print(json.dumps({"status": "HEALED_BY_PROBE", "trigger": trig, "route": rc2}))
            return 0
        res = backup_execute(tid, dry_run)
        if res.get("ok"):
            if not dry_run:
                st.setdefault("triggered", {})[tid] = now
                st["first_empty_at"] = None
                save_state(st)
                notify("BACKUP_TRIGGERED", "backup-trigger-" + tid,
                       "备工已自动接管卡单 %s，交付物已写出。" % tid)
            print(json.dumps({"status": "BACKUP_EXECUTED", "trigger": trig, **res}))
            return 0
        notify("NEEDS_DECISION", "backup-trigger-" + tid,
               "备工接管卡单 %s 失败：%s，需人工处理。" % (tid, res.get("reason")))
        print(json.dumps({"status": "BACKUP_FAILED", "trigger": trig, **res}))
        return 1
    finally:
        try:
            LOCK.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
