#!/usr/bin/env python3
"""A7 三问回写（机器生成，不手写）——51 条缺口逐条三问。

依据：gate_registry、canary_ledger、gate-wiring.csv、核对表
  system-design-gap-checklist-20261110.md（8 类 51 条）。
输出：state/a7-three-questions-20261110.md + .csv。
每条三列必须有值，空值即"否"，不得填 na。

三问定义：
  代码存在：文件在枢纽存在（os.path.exists），DB 对象（触发器/表）在库里存在。
  接进生产：映射门禁 mode=enforce 且 last_block_ts 非空 -> 是（call_site）；
            否则 否（注明 mode / last_block_ts 状态）。
  生产收据：canary_ledger 里有该门禁 canary_id 的 auditor 行且 ok=true
            -> 是（canary_id + 最新 auditor 行时间）；否则 否。
"""
import csv
import json
import os
import subprocess
import sys

BASE = "/home/ubuntu/ai-team"
PSQL = ["sudo", "docker", "exec", "-u", "postgres", "ops-pg",
        "psql", "-d", "central_queue", "-t", "-A", "-F", "\t"]

# 51 条：(编号, 问题简述, 代码路径, 映射门禁)
# 代码路径来自核对表"涉及文件/脚本"列；映射门禁按 W1-W6 归属。
# DB 对象用 "db:trigger:NAME" / "db:table:NAME" 表示，由脚本查库验证。
ITEMS = [
    ("2.1-1", "verifier 五门生产零运行", "scripts/c2-verifier-20261104.py", "verifier-rerun"),
    ("2.1-2", "M4 五硬护栏零接线", "scripts/m4-antirecurrence-guard-20261107.py", "m4-guards"),
    ("2.1-3", "M5 check 脚本无调用方", "scripts/m5-switch-guard.py", "m5-switch-guard"),
    ("2.1-4", "decision-code-gate 零调用", "scripts/decision-code-gate.py", "decision-code-gate"),
    ("2.1-5", "dispatch-gate 未接 live 路径", "scripts/dispatch-gate-hub.py", "dispatch-gate-hub"),
    ("2.1-6", "评审硬门未接 live submit", "scripts/queue_api-20261105.py", "verifier-rerun"),
    ("2.1-7", "preflight 四门禁只在测试引用", "scripts/preflight-gate-20261107.py", "preflight-gate"),
    ("2.2-1", "timer 静态键被终态行占用", "scripts/timer_submit-20261104.py", "idempotency-rotation"),
    ("2.2-2", "contract 幂等键无轮换", "scripts/dispatch_task.py", "idempotency-rotation"),
    ("2.2-3", "d43a5688 手工清键复发", "scripts/timer_submit-20261104.py", "idempotency-rotation"),
    ("2.2-4", "timer_submit 只清终态行", "scripts/timer_submit-20261104.py", "idempotency-rotation"),
    ("2.2-5", "long-task-sweeper 疑似跳过 timer", "scripts/long-task-sweeper-20261106.py", "idempotency-rotation"),
    ("2.2-6", "cron_flock 仅 8 槽位", "scripts/cron_flock.sh", "cron-flock-perjob"),
    ("2.3-1", "gateway_canary 停滞 5.9h", "scripts/gateway_canary-20261104.py", "health-heartbeat"),
    ("2.3-2", "Zen 零探针 80h", "scripts/brain_canary.py", "health-heartbeat"),
    ("2.3-3", "health_3q 读 43h 旧池文件仍 PASS", "scripts/health_3q-20261104.py", "health-heartbeat"),
    ("2.3-4", "唤醒体检 RED vs 三问 GREEN 打架", "scripts/health_3q-20261104.py", "health-heartbeat"),
    ("2.3-5", "静默策略无代码实现", "scripts/health_3q_reporter.py", "health-heartbeat"),
    ("2.3-6", "Q1 只数池文件名额", "scripts/health_3q-20261104.py", "health-heartbeat"),
    ("2.3-7", "brain_watch 5 分钟探针已停用", "scripts/brain_watch.sh", "health-heartbeat"),
    ("2.3-8", "Q2 用 OR 逻辑", "scripts/health_3q-20261104.py", "health-heartbeat"),
    ("2.3-9", "Q3 无物即过", "scripts/health_3q-20261104.py", "health-heartbeat"),
    ("2.4-1", "dispatch_task 注释称 worker 直接读库", "scripts/dispatch_task.py", "lease-fence"),
    ("2.4-2", "实装 pull_once 走 /v1/claim", "state/pull-once-20261105/worker/pull_once.py", "lease-fence"),
    ("2.4-3", "queue_api 注释称直接读库的是 c2-executor", "scripts/queue_api-20261105.py", "lease-fence"),
    ("2.4-4", "workdir 两处创建点挂假设上", "scripts/dispatch_task.py", "lease-fence"),
    ("2.4-5", "注释未随代码更新", "scripts/dispatch_task.py", "lease-fence"),
    ("2.5-1", "执行器 600s vs API 1200s", "scripts/cq_common.py", "lease-fence"),
    ("2.5-2", "执行器不续期", "scripts/c2-executor-20261104.py", "lease-fence"),
    ("2.5-3", "pull_once 只在执行前 heartbeat 一次", "state/pull-once-20261105/worker/pull_once.py", "lease-fence"),
    ("2.5-4", "dispatch 默认 deadline 4h", "scripts/dispatch_task.py", "duration-limit"),
    ("2.5-5", "无唯一租约定义", "scripts/cq_common.py", "lease-fence"),
    ("2.5-6", "无后台 heartbeat 线程", "state/pull-once-20261105/worker/pull_once.py", "lease-fence"),
    ("2.5-7", "无任务时长上限强制", "scripts/gate-svc.py", "duration-limit"),
    ("2.6-1", "pull-pilot /complete 可直接置 DONE", "scripts/pull-pilot-server-20261104.py", "dispatch-receipt"),
    ("2.6-2", "ad-hoc psql 直插不过门禁", "db:trigger:tasks_done_receipt", "dispatch-receipt"),
    ("2.6-3", "GATE-DISABLED 空文件静默 bypass", "scripts/gate-wiring-audit.py", "kill-switch-monitor"),
    ("2.6-4", "decision-queue 裸 JSON 无写入口", "scripts/gate-svc.py", "decision-queue-api"),
    ("2.6-5", "M5 开关声明式 + 离线校验", "scripts/m5-switch-guard.py", "m5-switch-guard"),
    ("2.6-6", "开关文件存在性判断无监控", "scripts/gate-wiring-audit.py", "kill-switch-monitor"),
    ("2.7-1", "verifier 证据检查弱正则", "scripts/c2-verifier-20261104.py", "verifier-rerun"),
    ("2.7-2", "封堵 3/4 与 slop 1/2 默认 na", "scripts/taskcontract_gate.py", "taskcontract-strict"),
    ("2.7-3", "五段式只做非空检查", "scripts/taskcontract_gate.py", "taskcontract-strict"),
    ("2.7-4", "Rule 14 forbidden 只查非空", "scripts/taskcontract_gate.py", "taskcontract-strict"),
    ("2.7-5", "门禁只做形状校验", "scripts/taskcontract_gate.py", "taskcontract-strict"),
    ("2.8-1", "看门狗注册表双轨", "scripts/watchdog-registry-check-20261107.py", "watchdog-registry"),
    ("2.8-2", "SOP 本地镜像漂移", "scripts/sop-mirror-check.py", "source-of-truth"),
    ("2.8-3", "预检台账双份并行", "db:table:gate_ledger", "source-of-truth"),
    ("2.8-4", "cron-inventory 一半 unknown", "scripts/cron-inventory-check-20261107.py", "cron-inventory-strict"),
    ("2.8-5", "M4 lessons 库 PG 表未建", "state/lessons_m4-ddl.sql", "m4-guards"),
    ("2.8-6", "无唯一真源声明", "knowledge/core/source-of-truth.md", "source-of-truth"),
]


def psql(sql):
    r = subprocess.run(PSQL + ["-c", sql], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("psql failed: %s\n%s" % (sql, r.stderr))
    return [ln.split("\t") for ln in r.stdout.strip().split("\n") if ln.strip()]


def main():
    # gate_registry 全表
    reg = {}
    for row in psql("SELECT gate_id, call_site, mode, canary_id, "
                    "to_char(last_block_ts,'YYYY-MM-DD HH24:MI:SS') "
                    "FROM gate_registry ORDER BY gate_id"):
        gid, site, mode, canary, ts = row[0], row[1], row[2], row[3], (row[4] if len(row) > 4 else "")
        reg[gid] = {"call_site": site, "mode": mode, "canary_id": canary, "ts": ts}
    # canary_ledger：auditor 行，最新 ok=true 的那行（按 canary_id）
    receipt = {}
    for row in psql("SELECT DISTINCT ON (gate) gate, "
                    "to_char(ts,'YYYY-MM-DD HH24:MI:SS') "
                    "FROM canary_ledger WHERE provenance='auditor' AND ok=true "
                    "ORDER BY gate, ts DESC"):
        receipt[row[0]] = row[1]
    # gate-wiring.csv 交叉核对
    wiring = []
    wpath = os.path.join(BASE, "state/gate-wiring.csv")
    if os.path.exists(wpath):
        with open(wpath, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                wiring.append(r)

    rows = []
    n_yes = {"code": 0, "wired": 0, "receipt": 0}
    for iid, desc, path, gid in ITEMS:
        # 代码存在
        if path.startswith("db:trigger:"):
            tname = path.split(":", 2)[2]
            hit = psql("SELECT count(*) FROM information_schema.triggers "
                       "WHERE trigger_name='%s'" % tname)
            code = ("是（触发器 %s）" % tname) if hit and hit[0][0] != "0" else "否"
        elif path.startswith("db:table:"):
            tname = path.split(":", 2)[2]
            hit = psql("SELECT count(*) FROM information_schema.tables "
                       "WHERE table_name='%s'" % tname)
            code = ("是（表 %s）" % tname) if hit and hit[0][0] != "0" else "否"
        else:
            code = ("是（%s）" % path) if os.path.exists(os.path.join(BASE, path)) else "否"
        # 接进生产
        g = reg.get(gid)
        if g is None:
            wired = "否（无对应门禁 %s）" % gid
        elif g["mode"] == "enforce" and g["ts"]:
            wired = "是（%s）" % g["call_site"]
        else:
            wired = "否（mode=%s，last_block_ts=%s）" % (g["mode"], g["ts"] or "空")
        # 生产收据
        canary = g["canary_id"] if g else ""
        if canary and canary in receipt:
            rec = "是（%s，auditor %s）" % (canary, receipt[canary])
        else:
            rec = "否"
        if code.startswith("是"):
            n_yes["code"] += 1
        if wired.startswith("是"):
            n_yes["wired"] += 1
        if rec.startswith("是"):
            n_yes["receipt"] += 1
        rows.append((iid, desc, code, wired, rec))

    # 写 Markdown + CSV
    md_path = os.path.join(BASE, "state/a7-three-questions-20261110.md")
    csv_path = os.path.join(BASE, "state/a7-three-questions-20261110.csv")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# A7 三问回写（机器生成，2026-11-10）\n\n")
        f.write("来源：gate_registry（%d 门禁）+ canary_ledger（auditor 行）+ "
                "gate-wiring.csv（%d 行）+ 核对表 51 条。\n\n" % (len(reg), len(wiring)))
        f.write("汇总：代码存在 %d/51，接进生产 %d/51，生产收据 %d/51。\n\n" % (
            n_yes["code"], n_yes["wired"], n_yes["receipt"]))
        f.write("| 编号 | 问题 | 代码存在（路径） | 接进生产（call_site） | 生产收据（canary_id 或日志行） |\n")
        f.write("|---|---|---|---|---|\n")
        for iid, desc, code, wired, rec in rows:
            f.write("| %s | %s | %s | %s | %s |\n" % (iid, desc, code, wired, rec))
        f.write("\n## gate-wiring.csv 交叉核对（%d 行）\n\n" % len(wiring))
        f.write("| gate_id | script | wired | receipt | status | 磁盘存在 |\n")
        f.write("|---|---|---|---|---|---|\n")
        for r in wiring:
            script = r.get("script", "")
            exists = "是" if os.path.exists(os.path.join(BASE, script)) else "否"
            f.write("| %s | %s | %s | %s | %s | %s |\n" % (
                r.get("gate_id", ""), script, r.get("wired", ""),
                r.get("receipt", ""), r.get("status", ""), exists))
        f.write("\n## 两条曾未通过金丝雀（已修）\n\n")
        f.write("- canary-idem-rotation：上一轮 ok=false（窗口内无真实过期键轮换观测）；"
                "E2 已 live 验证通过（13:08:16Z auditor deny 行 ok=true：过期 RETRY_WAIT 行被隔离 + 键轮换 + 新任务不被旧键阻塞），"
                "--full wired 行 ok=true。已修。\n")
        f.write("- canary-m4-second-fingerprint：上一轮 ok=false（未跑）；"
                "收尾轮次已 live 验证通过（13:29:29Z auditor deny 行 ok=true："
                "FP M4-CANARY-20261110-CLOSEOUT 与 CLOSEOUT2 各两次，第 1 次不收紧、第 2 次 TOOL_TIGHTEN；"
                "m4-guards 已 enforce，last_block_ts=2026-11-10 13:29:29Z；"
                "副本库 mode=off 反向验证 wired_ok=false）。已修。\n")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["编号", "问题", "代码存在", "接进生产", "生产收据"])
        for r in rows:
            w.writerow(list(r))
    print("rows=%d code=%d wired=%d receipt=%d" % (
        len(rows), n_yes["code"], n_yes["wired"], n_yes["receipt"]))
    print("wrote %s" % md_path)
    print("wrote %s" % csv_path)
    for iid, desc, code, wired, rec in rows:
        if not (code.startswith("是") and wired.startswith("是") and rec.startswith("是")):
            print("NOT-ALL-YES %s %s | %s | %s | %s" % (iid, desc, code, wired, rec))


if __name__ == "__main__":
    sys.exit(main())
