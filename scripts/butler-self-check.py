#!/usr/bin/env python3
# butler-self-check: the butler's own doctor (2026-10-30, ownership moved from Twt to the butler).
# The butler (24h resident) runs this daily: scores itself on tonight's SRE table
# (fast/slow lanes, three-color lines, 3-min backup takeover, model isolation, degradation),
# applies safe config fixes itself, and writes a self-heal log for Twt to translate.
# Below 80 -> RED ALERT pushed to the decision queue immediately.
# Safe auto-fixes only (reversible, no money, no external publish, no irreversible):
#   - dead model in opencode config -> remove it (config backed up first, JSON re-validated).
# Everything else becomes a repair task in the log (planned / in_progress);
# chairman-only items (strong-model API, scan-code login) are marked REDLINE, never auto-fixed.
# Date-gated: runs once per day unless --force (loop calls it every cycle, cron calls it daily).
import os, json, subprocess, sys, time, glob, shutil
BASE = "/home/ubuntu/ai-team"
STATE = os.path.join(BASE, "state")
OUT = os.path.join(BASE, "ceo-seat/out")
CFG = "/home/ubuntu/.config/opencode/opencode.json"
SELF = os.path.join(BASE, "scripts/self-health-check.py")
REDLINE_KEYS = ("strong-model", "api-key", "api_key", "scan", "login", "deepseek", "permission", "sanitize", "fake_receipt")
def run_score():
    r = subprocess.run([sys.executable, SELF], capture_output=True, text=True, timeout=300)
    try:
        d = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        d = {"score": 0, "dims": {}, "failed": []}
    # self-health-check prints failed as [dim, check] pairs; normalize to dicts.
    norm = []
    for f in d.get("failed", []):
        if isinstance(f, (list, tuple)) and len(f) >= 2:
            norm.append({"dimension": f[0], "check": f[1]})
        elif isinstance(f, dict):
            norm.append(f)
    d["failed"] = norm
    return d
def dead_model_fix():
    fixed = []
    try:
        cfg = json.load(open(CFG, encoding="utf-8"))
        models = ((cfg.get("provider") or {}).get("my-gateway") or {}).get("models") or {}
        log = ""
        try:
            log = open(os.path.join(STATE, "five-step-executor.log"), encoding="utf-8", errors="ignore").read()
        except Exception:
            pass
        for m in list(models.keys()):
            # conservative: remove only if the model never succeeded in the whole log and failed a lot
            import re as _re
            succ = len(_re.findall(r"STEP4_DONE[^\n]*" + _re.escape(m), log))
            fail = len(_re.findall(r"STEP4_FAIL[^\n]*" + _re.escape(m), log))
            if succ == 0 and fail >= 50:
                shutil.copy2(CFG, CFG + ".bak-butler-" + time.strftime("%Y%m%d"))
                del models[m]
                fixed.append(m)
        if fixed:
            json.dump(cfg, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            json.load(open(CFG, encoding="utf-8"))
    except Exception as e:
        fixed.append("fix_failed:" + str(e)[:200])
    return fixed
# Architecture review (first principles, 2026-10-30): the butler asks itself, in code,
# not as a doc: for each failed check, is it one-off or mechanism? what is the SRE best
# practice refactor? will it halt the factory tomorrow? Plus system-wide: single-mode queue?
# single-point dependency? unclear authority? Answers become REFACTOR tasks, not patches.
BEST_PRACTICE = {
    "deadlock_takeover_within_3min": "supervisor non-blocking + heartbeat (SRE supervision): supervisor never waits on workers",
    "backlog_small": "fast/slow lanes + bounded queues + backpressure (SRE load shedding)",
    "pool_depth_ge_5": "circuit breaker + multi-provider fallback (microservices resilience)",
    "pipeline_segmented_proof": "graceful degradation: segment long docs inline, not via rescue (SRE degradation)",
    "degraded_fired_live": "graceful degradation: time-boxed expert wait, then direct output with label",
}
HALT_RISK = {
    "deadlock_takeover_within_3min": True,
    "backlog_small": True,
    "pool_depth_ge_5": True,
    "pipeline_segmented_proof": False,
    "degraded_fired_live": False,
}
def arch_review(failed, dims, prev):
    prev = prev or []
    prev_set = set()
    for f in prev:
        if isinstance(f, dict):
            prev_set.add((f.get("dimension"), f.get("check")))
        elif isinstance(f, (list, tuple)) and len(f) >= 2:
            prev_set.add((f[0], f[1]))
    out = []
    for f in failed:
        dim, chk = f.get("dimension"), f.get("check")
        recurring = (dim, chk) in prev_set
        out.append({"dimension": dim, "check": chk,
                    "q1_oneoff_or_mechanism": "mechanism defect (recurring)" if recurring else "first occurrence (watch)",
                    "q2_best_practice": BEST_PRACTICE.get(chk, "SRE: measure, isolate, add fallback"),
                    "q3_halts_factory_tomorrow": HALT_RISK.get(chk, False),
                    "task_kind": "REFACTOR" if recurring or HALT_RISK.get(chk, False) else "watch"})
    # system-wide autonomous questions
    try:
        fe = open(os.path.join(BASE, "ceo-seat/work/five-step-executor.py"), encoding="utf-8", errors="ignore").read()
        rv = open(os.path.join(BASE, "scripts/butler_eight_layer_review.py"), encoding="utf-8", errors="ignore").read()
        cfg = json.load(open(CFG, encoding="utf-8"))
        nmodels = len(((cfg.get("provider") or {}).get("my-gateway") or {}).get("models") or {})
    except Exception:
        fe, rv, nmodels = "", "", 0
    sysq = [
        {"q": "queue single-mode?", "answer": "no (lanes live)" if ("_lane_for" in fe and "slot-%d" in fe) else "YES -> refactor: split lanes itself",
         "refactor": None if ("_lane_for" in fe and "slot-%d" in fe) else "split fast/slow lanes"},
        {"q": "dependency single-point?", "answer": "thin but not single (%d models)" % nmodels if nmodels >= 2 else "YES -> refactor: add fallback provider",
         "refactor": None if nmodels >= 2 else "add fallback provider"},
        {"q": "authority unclear?", "answer": "no (three-color lines live)" if "classify_decision" in rv else "YES -> refactor: draw three-color lines",
         "refactor": None if "classify_decision" in rv else "draw three-color lines"},
    ]
    return {"per_failed": out, "system_wide": sysq}


def track_repairs(rep, failed, tracker, now_ts):
    # Deadline tracking for repair tasks: in_progress -> 24h, planned/other -> 48h.
    # Due dates persist across runs (never reset while the task is unfinished).
    # A task is escalated only when it is BOTH past due AND still failing now.
    # Tracker entries for repairs that disappeared (fixed) are pruned.
    # Returns (tracked_repairs, overdue_list).
    failed_keys = set()
    for f in (failed or []):
        if isinstance(f, dict):
            failed_keys.add((f.get("dimension"), f.get("check")))
    cur_keys = set()
    tracked = []
    for r in (rep or []):
        dim, chk = r.get("dimension"), r.get("check")
        key = "%s/%s" % (dim, chk)
        cur_keys.add(key)
        ent = tracker.get(key) or {}
        due = ent.get("due_at")
        if not isinstance(due, (int, float)):
            horizon = 24 * 3600 if (r.get("status") == "in_progress") else 48 * 3600
            due = now_ts + horizon
            ent = {"due_at": due, "first_seen": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now_ts)), "status": r.get("status")}
            tracker[key] = ent
        else:
            ent["status"] = r.get("status")
            tracker[key] = ent
        escalated = (now_ts > due) and ((dim, chk) in failed_keys)
        item = dict(r)
        item["due_at"] = due
        item["escalated"] = escalated
        tracked.append(item)
    for key in list(tracker.keys()):
        if key not in cur_keys:
            del tracker[key]
    overdue = [t for t in tracked if t.get("escalated")]
    return tracked, overdue


def main():
    force = "--force" in sys.argv
    today = time.strftime("%Y-%m-%d")
    log_path = os.path.join(OUT, "butler-self-heal-log.md")
    try:
        existing = open(log_path, encoding="utf-8").read() if os.path.isfile(log_path) else ""
    except Exception:
        existing = ""
    if not force and ("## " + today) in existing:
        print(json.dumps({"butler_self_check": "already_ran_today", "date": today}))
        return
    # prev must be read BEFORE run_score overwrites self-health-latest.json, else everything looks recurring.
    try:
        _prev_raw = json.load(open(os.path.join(STATE, "self-health-latest.json"), encoding="utf-8")).get("failed", [])
    except Exception:
        _prev_raw = []
    sc = run_score()
    score = sc.get("score", 0)
    failed = sc.get("failed", [])
    arch = arch_review(failed, sc.get("dims", {}), _prev_raw)
    fixed = dead_model_fix()
    # repair tasks: fixed ones vs planned/in_progress (from the score engine's repair map)
    try:
        rep = json.load(open(os.path.join(STATE, "self-repair-log.json"), encoding="utf-8")).get("repairs", [])
    except Exception:
        rep = []
    # Deadline tracking: due dates persist across runs; overdue + still failing
    # => escalated in the log only (never a new queue entry per overdue item).
    try:
        _tracker = json.load(open(os.path.join(STATE, "self-repair-tracker.json"), encoding="utf-8"))
        if not isinstance(_tracker, dict):
            _tracker = {}
    except Exception:
        _tracker = {}
    rep, overdue = track_repairs(rep, failed, _tracker, time.time())
    try:
        json.dump(_tracker, open(os.path.join(STATE, "self-repair-tracker.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception:
        pass
    redline = []
    for f in failed:
        txt = (f.get("dimension", "") + " " + f.get("check", "")).lower()
        if any(k in txt for k in ("api", "scan", "login", "deepseek")):
            redline.append(f)
    alert = score < 80
    if alert:
        try:
            subprocess.run([sys.executable, os.path.join(BASE, "ceo-seat/work/notify.py"), "push", "--type", "BUTLER_SELF_HEALTH_RED", "--task-id", "butler-self-health-" + today, "--summary", "大管家自查 %d 分（低于80）：%s" % (score, "、".join("%s/%s" % (f.get("dimension"), f.get("check")) for f in failed))], capture_output=True, text=True, timeout=60)
        except Exception:
            pass
    dims = sc.get("dims", {})
    entry = []
    entry.append("## " + today + " 大管家自查自愈")
    entry.append("- 自评分：%d/100（%s）" % (score, "、".join("%s %d" % (k, v) for k, v in dims.items()) if dims else "无"))
    entry.append("- 红线报警：" + ("已推（低于80）" if alert else "无（≥80）"))
    entry.append("- 自己改配置修好的：" + ("、".join(fixed) if fixed else "无（今夜无需改配置）"))
    entry.append("- 修理任务：")
    for r in rep:
        _due = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["due_at"])) if r.get("due_at") else "无期限"
        _mark = " [超期升级]" if r.get("escalated") else ""
        entry.append("  - %s/%s：%s [%s] 期限：%s%s" % (r.get("dimension"), r.get("check"), r.get("fix"), r.get("status"), _due, _mark))
    if overdue:
        entry.append("- 超期升级（已超期且仍失败，仅记日志，不另推队列条目）：")
        for r in overdue:
            entry.append("  - %s/%s（期限 %s）" % (r.get("dimension"), r.get("check"), time.strftime("%Y-%m-%d %H:%M", time.localtime(r["due_at"]))))
    entry.append("- 架构审查（第一性原理）：")
    for a in arch.get("per_failed", []):
        entry.append("  - %s/%s：%s；最佳实践：%s；明日停摆：%s；任务：%s" % (a["dimension"], a["check"], a["q1_oneoff_or_mechanism"], a["q2_best_practice"], "是" if a["q3_halts_factory_tomorrow"] else "否", a["task_kind"]))
    for s in arch.get("system_wide", []):
        entry.append("  - 自诊：%s %s" % (s["q"], s["answer"]))
    if redline:
        entry.append("- 需董事长（红线）：")
        for f in redline:
            entry.append("  - %s/%s" % (f.get("dimension"), f.get("check")))
    entry.append("")
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(entry) + "\n")
    json.dump({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "score": score, "dims": dims, "failed": failed, "fixed": fixed, "alert": alert, "arch_review": arch}, open(os.path.join(STATE, "butler-self-health-latest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps({"butler_self_check": "done", "score": score, "alert": alert, "fixed": fixed, "log": log_path}))
if __name__ == "__main__":
    main()
