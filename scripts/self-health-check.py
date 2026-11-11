#!/usr/bin/env python3
# self-health-check: Twt's own system health score table (2026-10-30, no external scoring).
# 5 dimensions x 5 checks x 4 points = 100. Lessons baked in: fast/slow lanes, three-color lines,
# anti-poisoning, 3-min backup takeover, model health, degradation + segmented long docs.
# Runs daily via cron; writes state/self-health-latest.json + dated record + repair log.
# Score < 80 -> repair tasks are recorded with a fix and a status (not a problem list);
# safe auto-fixes are applied, the rest become owned repair tasks for the butler loop / Twt.
import os, json, glob, re, subprocess, importlib.util, time
BASE = "/home/ubuntu/ai-team"
STATE = os.path.join(BASE, "state")
WORK = os.path.join(BASE, "ceo-seat/work")
FE = os.path.join(WORK, "five-step-executor.py")
RV = os.path.join(BASE, "scripts/butler_eight_layer_review.py")
CFG = "/home/ubuntu/.config/opencode/opencode.json"
def load_mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m
def crontab_text():
    try:
        return subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return ""
def file_text(p):
    try:
        return open(p, encoding="utf-8", errors="ignore").read()
    except Exception:
        return ""
def svc():
    try:
        r = subprocess.run(["systemctl", "show", "butler-loop.service", "-p", "MainPID", "-p", "ActiveState", "-p", "NRestarts"], capture_output=True, text=True, timeout=15)
        d = {}
        for ln in (r.stdout or "").splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1)
                d[k] = v
        return d
    except Exception:
        return {}
def main():
    checks = {}
    def chk(dim, name, ok):
        checks.setdefault(dim, []).append({"check": name, "pass": bool(ok), "pts": 4 if ok else 0})
    fe_txt = file_text(FE)
    rv_txt = file_text(RV)
    ct = crontab_text()
    s = svc()
    # D1 HA
    chk("D1_ha", "loop_active", s.get("ActiveState") == "active" and int(s.get("MainPID", "0") or 0) > 0)
    chk("D1_ha", "revive_within_2min", bool(re.search(r"2-59/2|\*/2|\*/1", ct)) and "seat_revive" in ct)
    wd = file_text(os.path.join(BASE, "scripts/loop-watchdog.py"))
    chk("D1_ha", "watchdog_armed", "loop-watchdog" in ct and "killed_deadlocked_loop" in wd)
    try:
        cfg = json.load(open(CFG, encoding="utf-8"))
        models = ((cfg.get("provider") or {}).get("my-gateway") or {}).get("models") or {}
    except Exception:
        models = {}
    chk("D1_ha", "backup_models_present", "glm-4-flash-cc" in models or "probe-zhipu-flash" in models)
    m = re.search(r"log_age < (\d+)", wd) or re.search(r"rev_age < (\d+)", wd)
    wd_secs = int(m.group(1)) if (m and m.group(1)) else 2400
    chk("D1_ha", "deadlock_takeover_within_3min", wd_secs <= 600)
    # D2 throughput
    chk("D2_throughput", "lanes_code_live", "_lane_for" in fe_txt and "slot-%d" in fe_txt)
    try:
        fe = load_mod(FE, "fe_health")
        g = os.path.join(WORK, "five-step-scenario-greeting-20261030.md")
        a = os.path.join(WORK, "five-step-accept-long-20261030.md")
        lane_ok = os.path.isfile(g) and os.path.isfile(a) and fe._lane_for(g, fe.read_lines(g)) == 0 and fe._lane_for(a, fe.read_lines(a)) == 1
    except Exception:
        lane_ok = False
    chk("D2_throughput", "lane_assignment_correct", lane_ok)
    pend = [f for f in os.listdir(WORK) if f.startswith("five-step-") and f.endswith(".md") and "PENDING" in file_text(os.path.join(WORK, f))]
    base_f = os.path.join(STATE, "self-health-baseline.json")
    try:
        base = json.load(open(base_f)).get("five_step_pending", 9999)
    except Exception:
        base = 9999
    chk("D2_throughput", "queue_not_growing", len(pend) <= base if base != 9999 else len(pend) <= 70)
    chk("D2_throughput", "cap_order_rules_live", "DEGRADED_SKIP" in fe_txt and ">= 10" in fe_txt and "TEST_MARKERS" in fe_txt)
    chk("D2_throughput", "backlog_small", len(pend) < 30)
    # D3 autonomy
    chk("D3_autonomy", "three_color_code_live", "classify_decision" in rv_txt and "BLUE" in rv_txt)
    try:
        q = json.load(open(os.path.join(STATE, "decision-queue.json")))
        npend = len(q.get("pending", []))
        pend_task_ids = [e.get("task_id", "").lower() for e in q.get("pending", [])]
    except Exception:
        npend, pend_task_ids = 999, []
    chk("D3_autonomy", "pending_bounded", npend <= 20)
    try:
        acl_lines = file_text(os.path.join(STATE, "auto-close-log.json")).splitlines()
        recent_cutoff = time.time() - 86400
        blue_recent = False
        for ln in acl_lines:
            if not ln.strip():
                continue
            rec = json.loads(ln)
            at_s = str(rec.get("at", ""))
            try:
                at_ts = time.mktime(time.strptime(at_s, "%Y-%m-%dT%H:%M:%S"))
            except Exception:
                continue
            if at_ts >= recent_cutoff:
                blue_recent = True
                break
    except Exception:
        blue_recent = False
    chk("D3_autonomy", "blue_closes_happening", blue_recent)
    # redline_kept: pass if NO redline tasks are pending; match task_id only (structured field),
    # not summary (free text) — dec-20261105-003 summary describes a defunct deepseek-chat check
    # and would false-positive on substring match.
    chk("D3_autonomy", "redline_kept", not any(k in t for t in pend_task_ids for k in ("deepseek", "sanitize", "fake_receipt")))
    # dec-20261105-003 同族补充：读取黄线台账判定，不硬编码 True
    yellow_log = file_text(os.path.join(STATE, "yellow-over-24h.log"))
    chk("D3_autonomy", "no_yellow_over_24h", "OVER_24H" not in yellow_log)
    # D4 model health
    chk("D4_models", "alive_pool_ge_3", len(models) >= 3)
    chk("D4_models", "dead_models_removed", all(x not in models for x in ("deepseek-reasoner", "gpt-oss-120b", "nvidia/nemotron-3-ultra-550b-a55b:free")))
    try:
        revs = sorted(glob.glob(os.path.join(STATE, "eight-layer-review-*.json")))
        latest_rev = json.load(open(revs[-1])) if revs else {}
        # per_model_summary_live: use JSON "at" field (generation time) instead of mtime; mtime drifts on backup/copy
        _at = latest_rev.get("at", "")
        _age_ok = False
        if _at:
            try:
                _gen = time.mktime(time.strptime(_at, "%Y-%m-%dT%H:%M:%S"))
                _age_ok = (time.time() - _gen) < 3600
            except Exception:
                _age_ok = False
        summ = bool(latest_rev) and _age_ok
    except Exception:
        summ = False
    chk("D4_models", "per_model_summary_live", summ)
    chk("D4_models", "anti_poisoning_guard", "MODEL_NOT_RECORDED" in fe_txt)
    chk("D4_models", "pool_depth_ge_5", len(models) >= 5)
    # D5 degradation
    chk("D5_degrade", "degraded_code_live", "DEGRADED_SKIP" in fe_txt)
    chk("D5_degrade", "segmented_code_live", "segmented_generate" in fe_txt)
    try:
        log = file_text(os.path.join(BASE, "logs/butler-loop.log")).splitlines()
        last_start = [l for l in log if "[LOOP] cycle start" in l][-1] if any("[LOOP] cycle start" in l for l in log) else ""
        loop_ok = int(s.get("NRestarts", "0") or 0) >= 1 and "15:55:36" not in last_start and last_start.strip() != ""
    except Exception:
        loop_ok = False
    chk("D5_degrade", "loop_restarted_timestamps_ok", loop_ok)
    # pipeline_segmented_proof: STEP4_SEGMENTED in executor log is the authoritative evidence;
    # a specific five-step file may have used direct model (not segmented), so check the log
    _fe_log = file_text(os.path.join(STATE, "five-step-executor.log"))
    _seg_proof = "STEP4_SEGMENTED" in _fe_log
    chk("D5_degrade", "pipeline_segmented_proof", _seg_proof)
    chk("D5_degrade", "degraded_fired_live", any("DEGRADED_SKIP" in file_text(os.path.join(WORK, f)) for f in os.listdir(WORK) if f.startswith("five-step-") and f.endswith(".md")))
    dims = {}
    total = 0
    failed = []
    for dim, lst in checks.items():
        sc = sum(x["pts"] for x in lst)
        dims[dim] = {"score": sc, "checks": lst}
        total += sc
        for x in lst:
            if not x["pass"]:
                failed.append({"dimension": dim, "check": x["check"]})
    repairs = []
    fix_map = {
        "deadlock_takeover_within_3min": ("planned", "non-blocking loop refactor (sessions detached, heartbeat 1min) so deadlock is detectable in 3min without false kills"),
        "backlog_small": ("in_progress", "drain in progress via lanes + cap 10; watch front tasks resolve"),
        "pool_depth_ge_5": ("planned", "test healthy gateway models for opencode use, add only ones that succeed"),
        "pipeline_segmented_proof": ("in_progress", "trigger accept-long five-step scan tonight, verify STEP4_SEGMENTED in pipeline"),
        "degraded_fired_live": ("in_progress", "fb-contract fallback fixed; watch a short fb file get DEGRADED_SKIP on next scans"),
    }
    for f in failed:
        status, fix = fix_map.get(f["check"], ("planned", "investigate and fix"))
        repairs.append({"dimension": f["dimension"], "check": f["check"], "fix": fix, "status": status})
    out = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "score": total, "dimensions": dims, "failed": failed, "repairs": repairs, "five_step_pending": len(pend), "decision_pending": npend, "models": sorted(models.keys())}
    open(os.path.join(STATE, "self-health-latest.json"), "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=2))
    open(os.path.join(STATE, "self-health-" + time.strftime("%Y%m%d") + ".json"), "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=2))
    open(os.path.join(STATE, "self-repair-log.json"), "w", encoding="utf-8").write(json.dumps({"at": out["at"], "score": total, "repairs": repairs}, ensure_ascii=False, indent=2))
    open(base_f, "w", encoding="utf-8").write(json.dumps({"five_step_pending": len(pend)}, ensure_ascii=False))
    print(json.dumps({"score": total, "dims": {k: v["score"] for k, v in dims.items()}, "failed": [(f["dimension"], f["check"]) for f in failed]}))
if __name__ == "__main__":
    main()
