#!/usr/bin/env python3
"""gate-wiring-audit.py -- 每日扫全部门禁的调用点与生产收据。

依据：system-design-20261107.md §2.1 改进项（P1-4）：“每个门禁必须有接线验收
（生产日志有收据、回归样本被拒过）；新增 gate-wiring-audit.py 每日扫全部门禁
的调用点与生产收据。”

对每个门禁查三问：
  1. 代码存在吗（文件在不在）？
  2. 接进生产路径了吗（cron 有没有跑它、其他脚本有没有调它）？
  3. 有生产收据吗（日志文件存在且 48 小时内有新内容，或有明确的拦截记录）？

结论四档：PASS（存在+接线+有收据）/ NOWIRING（存在但未接线）/ NORECEIPT
（接线但无收据）/ MISSING（代码不存在）。
输出：stdout 报告 + 日志 state/logs/gate-wiring-audit.log；有 FAIL 项写 outbox 告警。
用法：python3 scripts/gate-wiring-audit.py [--outbox]
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

BASE = os.path.expanduser("~/ai-team")
SCRIPTS = os.path.join(BASE, "scripts")
STATE = os.path.join(BASE, "state")
LOGS = os.path.join(BASE, "logs")
OUTBOX = os.path.join(STATE, "outbox")

# 门禁清单：(编号, 名称, 脚本路径, 调用点证据, 收据证据)
# 调用点证据：cron 关键字或调用方脚本；收据证据：日志文件（48h 内有写入）。
GATES = [
    ("2.1-1", "verifier 五门", "scripts/c2-verifier-20261104.py",
     ["c2-verifier", "verifier"], ["state/logs/queue-api-audit.log", "logs/verifier.log"]),
    ("2.1-2", "M4 五硬护栏", "scripts/m4-antirecurrence-guard-20261107.py",
     ["m4-", "morning-metrics", "boot-quiz"], ["state/logs/m4-guard.log", "logs/m4-guard.log"]),
    ("2.1-3", "M5 check", "scripts/check-m5-switch.sh",
     ["check-m5-switch", "m5-window-check"], ["logs/m5-window-check.log"]),
    ("2.1-4", "decision-code-gate", "scripts/decision-code-gate.py",
     ["decision-code-gate"], ["state/logs/decision-code-gate.log"]),
    ("2.1-5", "dispatch-gate", "scripts/dispatch-gate-hub.py",
     ["dispatch-gate"], ["state/logs/dispatch-gate.log", "state/logs/codegen-20261110.log"]),
    ("2.1-6", "评审硬门", "scripts/queue_api-20261105.py",
     ["REVIEW_ENFORCE", "queue_api"], ["state/logs/queue-api-audit.log"]),
    ("2.1-7", "preflight 四门禁", "scripts/preflight-check.py",
     ["preflight"], ["state/logs/preflight.log"]),
    ("3-3", "四类门", "scripts/four-category-gate.py",
     ["four-category-gate"], ["state/logs/four-category-gate.log"]),
    ("new-1", "implementation-gate", "scripts/implementation-gate.sh",
     ["implementation-gate"], ["state/logs/implementation-gate-ledger.jsonl"]),
    ("new-2", "seat-wake-gate", "scripts/seat-wake-gate.sh",
     ["seat-wake-gate"], ["state/logs/seat-wake-gate-ledger.jsonl"]),
    ("new-3", "answer-gate", "scripts/answer-gate.sh",
     ["answer-gate"], ["state/logs/answer-gate-ledger.jsonl"]),
    ("2.3-3", "health_3q", "scripts/health_3q-20261104.py",
     ["health_3q"], ["state/logs/health-3q.log", "logs/health-3q.log", "state/logs/beszel-health.log"]),
    ("2.3-1", "gateway_canary", "scripts/timer_submit-20261104.py",
     ["gateway_canary"], ["state/logs/timer-submit.log"]),
    ("2.2-1", "timer_submit 轮换", "scripts/timer_submit-20261104.py",
     ["timer_submit"], ["state/logs/timer-submit.log"]),
]


def file_exists(rel):
    return os.path.isfile(os.path.join(BASE, rel))


def grep_in_scripts(keywords, self_name):
    """在 scripts/ 与 crontab 里找调用点（排除被查脚本自身）。

    只搜 *.py / *.sh 源码文件：__pycache__/*.pyc（编译字节码含关键词串）、
    *.bak* 备份、*.md 文档都不是活调用方，2026-11-10 假阳性教训
    （第二次运行把 .pyc 计为调用方，10/14 inflated，首跑 4/14 才接近真实）。
    """
    hits = []
    for kw in keywords:
        try:
            r = subprocess.run(
                ["grep", "-rl", "--include=*.py", "--include=*.sh",
                 "--exclude-dir=__pycache__", kw, SCRIPTS],
                capture_output=True, text=True, timeout=60
            )
            for f in r.stdout.splitlines():
                if os.path.basename(f) != self_name:
                    hits.append(f)
        except Exception:
            pass
        try:
            r = subprocess.run(
                ["crontab", "-l"], capture_output=True, text=True, timeout=30
            )
            if kw in r.stdout:
                hits.append("crontab:" + kw)
        except Exception:
            pass
    return sorted(set(hits))


def receipt_fresh(rel_paths, hours=48):
    """收据文件存在且 hours 内有写入（或文件本身就是 ledger 且有内容）。"""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    for rel in rel_paths:
        p = os.path.join(BASE, rel)
        if not os.path.isfile(p):
            continue
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(p), tz=timezone.utc)
            if mtime >= cutoff and os.path.getsize(p) > 0:
                return rel, mtime.isoformat()
        except Exception:
            continue
    return None, None


def kill_switch_check():
    """Cutover51 W2.2/W2.3: kill-switch monitor. DB gate_overrides (unexpired)
    are the ONLY legal way to disable a gate; GATE-DISABLED /
    REVIEW-HOOK-DISABLED files anywhere under the hub must NOT exist and are
    never read by any gate. Any appearance or change -> alert.
    Returns a dict; state snapshot in state/logs/kill-switch-state.json."""
    import glob
    state_path = os.path.join(STATE, "logs", "kill-switch-state.json")
    files_found = []
    for pat in ("GATE-DISABLED*", "REVIEW-HOOK-DISABLED*"):
        for p in glob.glob(os.path.join(BASE, "**", pat), recursive=True):
            if ".bak" not in p and "__pycache__" not in p:
                files_found.append(os.path.relpath(p, BASE))
    overrides = []
    try:
        dsn = ""
        for line in open(os.path.join(STATE, ".cq-dsn")):
            line = line.strip()
            if line.startswith("CQ_DSN="):
                dsn = line[len("CQ_DSN="):].strip().strip('"').strip("'")
                break
        if dsn:
            import psycopg
            with psycopg.connect(dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT gate_id, reason, expires_at FROM public.gate_overrides WHERE expires_at > now()")
                    overrides = [{"gate_id": r[0], "reason": r[1], "expires_at": r[2].isoformat()} for r in cur.fetchall()]
    except Exception as e:
        overrides = [{"error": str(e)[:120]}]
    snap = {"files": sorted(files_found), "overrides": overrides}
    prev = None
    try:
        prev = json.load(open(state_path))
    except Exception:
        prev = None
    changed = (prev is not None and prev != snap)
    first = prev is None
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    alert = bool(files_found) or changed
    return {"files_found": files_found, "overrides": overrides,
            "changed": changed, "first_run": first, "alert": alert}


def main():
    outbox = "--outbox" in sys.argv
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for gid, name, script, keywords, receipts in GATES:
        exists = file_exists(script)
        if not exists:
            # 有些门禁是 .sh 或在 state/ 下，宽容查一下同名
            base = os.path.basename(script)
            alt = subprocess.run(
                ["find", SCRIPTS, "-name", base], capture_output=True, text=True, timeout=30
            ).stdout.strip()
            exists = bool(alt)
            if exists:
                script = os.path.relpath(alt.splitlines()[0], BASE)
        callers = grep_in_scripts(keywords, os.path.basename(script)) if exists else []
        # 调用点至少要有一个非自身的引用（crontab 或其他脚本）
        wired = bool(callers)
        receipt, rtime = receipt_fresh(receipts) if exists else (None, None)
        if not exists:
            verdict = "MISSING"
        elif not wired:
            verdict = "NOWIRING"
        elif not receipt:
            verdict = "NORECEIPT"
        else:
            verdict = "PASS"
        results.append({
            "id": gid, "name": name, "script": script,
            "exists": exists, "wired": wired, "callers": callers[:5],
            "receipt": receipt, "receipt_time": rtime, "verdict": verdict,
        })
    npass = sum(1 for r in results if r["verdict"] == "PASS")
    nfail = len(results) - npass
    ks = kill_switch_check()
    report = {
        "ts": now, "total": len(results), "pass": npass, "fail": nfail,
        "results": results, "kill_switch": ks,
    }
    log_path = os.path.join(STATE, "logs", "gate-wiring-audit.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a") as f:
        f.write(json.dumps(report, ensure_ascii=False) + "\n")
    print(f"gate-wiring-audit {now} total={len(results)} PASS={npass} FAIL={nfail}")
    print(f"  kill-switch: files={ks['files_found']} overrides={len(ks['overrides'])} changed={ks['changed']} alert={ks['alert']}")
    for r in results:
        print(f"  [{r['verdict']:9s}] {r['id']:6s} {r['name']:20s} script={r['script']} "
              f"wired={r['wired']} receipt={r['receipt']}")
    if ks["alert"] and outbox:
        os.makedirs(OUTBOX, exist_ok=True)
        alert = os.path.join(
            OUTBOX, f"kill-switch-{datetime.now(timezone.utc):%Y%m%d-%H%M}.md")
        with open(alert, "w") as f:
            f.write(f"# kill-switch 告警（{now}）\n\n- 开关文件: {ks['files_found']}\n"
                    f"- 未过期 overrides: {ks['overrides']}\n- 变化: {ks['changed']}\n")
        print(f"kill-switch 告警已写 {alert}")
    if nfail > 0 and outbox:
        os.makedirs(OUTBOX, exist_ok=True)
        fail_list = [r for r in results if r["verdict"] != "PASS"]
        alert = os.path.join(
            OUTBOX, f"gate-wiring-audit-{datetime.now(timezone.utc):%Y%m%d-%H%M}.md")
        with open(alert, "w") as f:
            f.write(f"# 门禁接线审计告警（{now}）\n\n{len(fail_list)} 项未通过：\n\n")
            for r in fail_list:
                f.write(f"- [{r['verdict']}] {r['id']} {r['name']} "
                        f"（script={r['script']}，wired={r['wired']}，"
                        f"receipt={r['receipt']}）\n")
        print(f"告警已写 {alert}")
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
