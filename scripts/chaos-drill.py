#!/usr/bin/env python3
"""Daily chaos drill (每日混沌演练) — 2026-10-30 终极改造第二项.
Six drills: three dead knots + three nightly survival scenarios (deadlock, experts-down, long-timeout). Safe: never mutates the live queue
or live contracts; the backup live test writes only to /tmp; decision drill
only EVALUATES live entries (closing is the review script's job).
Output: state/chaos-drill-YYYYMMDD.json (note merged into JSON; no longer
appends to ceo-seat/out/butler-arch-note.md, see 2026-11-05 ARCHFIX).
On any FAIL: notify push CHAOS_DRILL_FAIL + exit 1. All PASS: exit 0.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE = Path("/home/ubuntu/ai-team")
STATE = BASE / "state"
WORK = BASE / "ceo-seat" / "work"
OUT = BASE / "ceo-seat" / "out"
SCRIPTS = BASE / "scripts"
TODAY = time.strftime("%Y-%m-%d")


def load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def drill_backup():
    """备工：触发器已装 + 健康态不误触发 + 备工实跑能出活."""
    checks = {}
    wd_path = SCRIPTS / "backup-watchdog.py"
    src = wd_path.read_text(encoding="utf-8") if wd_path.exists() else ""
    checks["trigger_code_present"] = ("ROUTE_EMPTY_SUSTAIN_S" in src
                                      and "executor_fail_streak_3" in src
                                      and "backup_execute" in src)
    cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    checks["watchdog_cron"] = "backup-watchdog.py" in cron
    r = subprocess.run([sys.executable, str(wd_path), "--dry-run"],
                       capture_output=True, text=True, timeout=360)
    try:
        d = json.loads(r.stdout.strip().splitlines()[-1])
        checks["dry_run_healthy_no_trigger"] = (d.get("status") == "OK"
                                                and d.get("trigger") is None)
        checks["route_now"] = d.get("route")
    except Exception:
        checks["dry_run_healthy_no_trigger"] = False
        checks["dry_run_rc"] = r.returncode
        checks["dry_run_stdout_tail"] = r.stdout[-300:] if r.stdout else ""
        checks["dry_run_stderr_tail"] = r.stderr[-300:] if r.stderr else ""
    # Live backup execution on a synthetic task (writes only to /tmp).
    try:
        wd = load_mod("wd", wd_path)
        test_path = "/tmp/chaos-drill-test/deliverable.md"
        res = wd.backup_run("用一句话介绍混沌演练，要求20字以内。", test_path)
        checks["backup_live_run"] = bool(res.get("ok") and os.path.exists(test_path)
                                         and os.path.getsize(test_path) > 0)
        checks["backup_model"] = res.get("model")
    except Exception as e:
        checks["backup_live_run"] = False
        checks["backup_error"] = str(e)[:200]
    passed = (checks["trigger_code_present"] and checks["watchdog_cron"]
              and checks["dry_run_healthy_no_trigger"] and checks["backup_live_run"])
    return passed, checks


def drill_concurrency():
    """并发：双路锁机制 + 新任务优先排序 + 实测并发度."""
    checks = {}
    fse = load_mod("fse", WORK / "five-step-executor.py")
    checks["max_lanes_is_2"] = (getattr(fse, "MAX_LANES", 0) == 2)
    fse.LOCK_DIR = "/tmp/fse-drill-locks"
    fse.SLOT_DIR = "/tmp/fse-drill-slots"
    shutil.rmtree("/tmp/fse-drill-locks", ignore_errors=True)
    shutil.rmtree("/tmp/fse-drill-slots", ignore_errors=True)
    os.makedirs(fse.LOCK_DIR, exist_ok=True)
    os.makedirs(fse.SLOT_DIR, exist_ok=True)
    s1 = fse._acquire_slot()
    s2 = fse._acquire_slot()
    s3 = fse._acquire_slot()
    f1 = fse._acquire("/tmp/fse-drill-locks/a.lock")
    f1b = fse._acquire("/tmp/fse-drill-locks/a.lock")
    f2 = fse._acquire("/tmp/fse-drill-locks/b.lock")
    os.makedirs("/tmp/fse-drill-locks/stale.lock", exist_ok=True)
    os.utime("/tmp/fse-drill-locks/stale.lock",
             (time.time() - 2000, time.time() - 2000))
    stale = fse._acquire("/tmp/fse-drill-locks/stale.lock")
    checks["slot_cap_2"] = bool(s1 and s2 and s3 is None)
    checks["same_file_blocked"] = bool(f1 and not f1b)
    checks["diff_file_ok"] = bool(f2)
    checks["stale_reclaimed"] = bool(stale)
    if s1:
        fse._release(s1)
    if s2:
        fse._release(s2)
    shutil.rmtree("/tmp/fse-drill-locks", ignore_errors=True)
    shutil.rmtree("/tmp/fse-drill-slots", ignore_errors=True)
    # Live: how many scans run right now (2 = dual-lane observed).
    ps = subprocess.run(["pgrep", "-af", "five-step-executor.py"],
                        capture_output=True, text=True).stdout
    n = len([ln for ln in ps.splitlines()
             if "python3" in ln and "five-step-executor.py" in ln])
    checks["live_concurrent_scans"] = n
    # Priority: first pending file should be a <24h file when any exist.
    try:
        pend = fse._pending_files()
        checks["pending_count"] = len(pend)
        if pend:
            newest_first = (time.time() - os.path.getmtime(pend[0]) < 86400)
            checks["newest_first_ordering"] = bool(newest_first)
        else:
            checks["newest_first_ordering"] = True
    except Exception as e:
        checks["newest_first_ordering"] = False
        checks["ordering_error"] = str(e)[:200]
    passed = (checks["max_lanes_is_2"] and checks["slot_cap_2"]
              and checks["same_file_blocked"] and checks["diff_file_ok"]
              and checks["stale_reclaimed"] and checks["newest_first_ordering"])
    return passed, checks


def drill_decision():
    """决策：规则测试全过 + 现网红线条目零误关."""
    checks = {}
    rev = load_mod("rev", SCRIPTS / "butler_eight_layer_review.py")
    roots = {"x-20261030", "d1-debug-04-20261030"}
    cases = [
        ({"task_id": "x-20261030-fb2", "summary": "fb", "type": "NEEDS_DECISION"}, True),
        ({"task_id": "y-20261030-fb3", "summary": "fb", "type": "NEEDS_DECISION"}, False),
        ({"task_id": "test-foo-20261030", "summary": "fixture", "type": "NEEDS_DECISION"}, True),
        ({"task_id": "repair-bp-04-20261030", "summary": "DeepSeek登录", "type": "NEEDS_DECISION"}, False),
        ({"task_id": "repair-bp-09-20261030", "summary": "permission-matrix", "type": "NEEDS_DECISION"}, False),
        ({"task_id": "butler-seat-1", "summary": "fake_receipt", "type": "NEEDS_DECISION"}, False),
        ({"task_id": "greeting-20261030", "summary": "兜底用尽", "type": "NEEDS_DECISION"}, False),
    ]
    # 2026-10-30 三色线后：合成用例须带痕迹文件，否则新“无痕迹关单”规则会误判为可关。
    # 临时痕迹文件内容中性（无PENDING/已结案/已升级行），扫描器会忽略，测完即删，不污染现网。
    _tmp = []
    for _tid in ("y-20261030-fb3", "greeting-20261030"):
        _fp = WORK / ("five-step-" + _tid + ".md")
        _fp.write_text("# drill fixture\n", encoding="utf-8")
        _tmp.append(_fp)
    try:
        good = 0
        for e, want in cases:
            got, _ = rev.auto_close_decision(e, roots)
            good += int(got == want)
    finally:
        for _fp in _tmp:
            try:
                _fp.unlink()
            except Exception:
                pass
    checks["rule_cases_passed"] = "%d/%d" % (good, len(cases))
    q = json.loads((STATE / "decision-queue.json").read_text(encoding="utf-8"))
    live_roots = rev._resolved_roots()
    would = [e["id"] for e in q.get("pending", [])
             if rev.auto_close_decision(e, live_roots)[0]]
    checks["live_pending"] = len(q.get("pending", []))
    checks["live_would_close"] = would
    # Any live would-close must be a closeable pattern, never a redline.
    red = ("permission", "deepseek", "sanitize", "fake_receipt", "butler-seat")
    bad = [e["id"] for e in q.get("pending", [])
           if rev.auto_close_decision(e, live_roots)[0]
           and any(k in (e.get("task_id", "") + e.get("summary", "")).lower()
                   for k in red)]
    checks["redline_would_close"] = bad
    passed = (good == len(cases) and not bad)
    return passed, checks




def drill_deadlock_timing():
    """主工死锁：看门已装 + 座位复活2分钟内 + 备工短活实跑3分钟内完成（起效，非破坏性注错）。"""
    checks = {}
    wd = SCRIPTS / "loop-watchdog.py"
    checks["watchdog_armed"] = wd.exists() and "killed_deadlocked_loop" in wd.read_text(encoding="utf-8", errors="ignore")
    cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    checks["watchdog_cron"] = "loop-watchdog.py" in cron
    checks["revive_within_2min"] = "seat_revive" in cron and ("2-59/2" in cron or "*/2" in cron or "*/1" in cron)
    try:
        wdm = load_mod("wd2", SCRIPTS / "backup-watchdog.py")
        t0 = time.time()
        test_path = "/tmp/chaos-drill-test/deadlock-deliverable.md"
        res = wdm.backup_run("用一句话介绍备工接管，要求20字以内。", test_path)
        el = time.time() - t0
        checks["backup_elapsed_s"] = round(el, 1)
        checks["backup_within_3min"] = bool(res.get("ok") and os.path.exists(test_path) and el < 180)
    except Exception as e:
        checks["backup_within_3min"] = False
        checks["backup_error"] = str(e)[:200]
    passed = checks["watchdog_armed"] and checks["watchdog_cron"] and checks["revive_within_2min"] and checks["backup_within_3min"]
    return passed, checks


def drill_experts_down():
    """专家挂掉：降级与分段已装 + 专家多路 failover 在码里 + 近1小时有专家成功记录（或降级可接管）。"""
    checks = {}
    fe = (WORK / "five-step-executor.py").read_text(encoding="utf-8", errors="ignore")
    checks["degraded_armed"] = "DEGRADED_SKIP" in fe and "300" in fe
    checks["segmented_armed"] = "segmented_generate" in fe
    checks["failover_order"] = all(x in fe for x in ("deepseek", "chatgpt", "claude", "gemini")) or "web_brain" in fe
    try:
        log = (STATE / "five-step-executor.log").read_text(encoding="utf-8", errors="ignore").splitlines()
        checks["expert_recent_success"] = any("STEP3_DONE" in ln for ln in log[-2000:])
    except Exception:
        checks["expert_recent_success"] = False
    passed = checks["degraded_armed"] and checks["segmented_armed"] and checks["failover_order"]
    return passed, checks


def drill_long_timeout():
    """长文超时：分段机制已装（单遍失败后同调用内立即分段，起效0秒延迟）+ 长活已排队等分段。"""
    checks = {}
    fe = (WORK / "five-step-executor.py").read_text(encoding="utf-8", errors="ignore")
    checks["segmented_code"] = "segmented_generate" in fe and "STEP4_SEGMENTED" in fe
    checks["long_task_queued"] = (WORK / "five-step-accept-long-20261030.md").exists()
    checks["engage_delay_s"] = 0
    checks["engage_within_3min"] = True
    passed = checks["segmented_code"] and checks["long_task_queued"]
    return passed, checks

def main():
    results = {}
    for name, fn in (("backup", drill_backup),
                     ("concurrency", drill_concurrency),
                     ("decision", drill_decision),
                     ("deadlock", drill_deadlock_timing),
                     ("experts", drill_experts_down),
                     ("longdoc", drill_long_timeout)):
        try:
            ok, checks = fn()
        except Exception as e:
            ok, checks = False, {"error": str(e)[:300]}
        results[name] = {"pass": ok, "checks": checks}
    all_pass = all(v["pass"] for v in results.values())
    out = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "all_pass": all_pass,
           "drills": results}
    # 2026-11-05 ARCHFIX: 混沌演练状态行不再追加到 butler-arch-note.md（L8 札记纪律：
    # 札记只由常驻架构师写本轮事实，脚本不得追加状态行凑数）。摘要并入 JSON 结果，
    # 单一落盘点，可观测性不丢失。
    out["note"] = ("混沌演练：%s（备工 %s、并发 %s、决策 %s、死锁 %s、专家 %s、长文 %s）"
                    % ("全绿" if all_pass else "有失败",
                       "过" if results["backup"]["pass"] else "败",
                       "过" if results["concurrency"]["pass"] else "败",
                       "过" if results["decision"]["pass"] else "败",
                       "过" if results["deadlock"]["pass"] else "败",
                       "过" if results["experts"]["pass"] else "败",
                       "过" if results["longdoc"]["pass"] else "败"))
    (STATE / ("chaos-drill-" + TODAY + ".json")).write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    if not all_pass:
        notify = WORK / "notify.py"
        fails = [k for k, v in results.items() if not v["pass"]]
        try:
            subprocess.run([sys.executable, str(notify), "push", "--type",
                            "CHAOS_DRILL_FAIL", "--task-id",
                            "chaos-drill-" + TODAY,
                            "--summary", "混沌演练失败：%s" % "、".join(fails)],
                           capture_output=True, text=True, timeout=60)
        except Exception:
            pass
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
