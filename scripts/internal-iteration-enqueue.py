#!/usr/bin/env python3
"""internal-iteration-enqueue.py — 基线下一轮内部迭代自动排队（2026-11-12）.

来源：董事长 2026-11-11 17:55 定迭代双轨，内部迭代清单 = 基线下一轮清单
（P1 卡住 READY、P2 m5、P4 回执模型、P5 intel mtime 写者）+ gate-svc 试点 PRD；
基线文件 state/baselines/baseline-20261111.md §4。
2026-11-12 21:09 主会话已关单 P1（任务 8934d884 QUARANTINED），故本脚本
只排 P2 / P4 / P5 三项；P1 每次运行只做只读复核（READY 复现即告警，不重排）。

行为（幂等，可每周 cron 跑）：
  1. 为每项准备合同 JSON（state/internal-iteration-20261112/<slug>-contract.json，
     task_type=script，执行体为 scripts/iter-diagnose-20261112.py）与 PLAN 文档
     （state/<slug>/PLAN-<slug>.md，八段齐全），缺失才写、已有不覆盖；
  2. 记 preflight 台账（四问，诚实填写）；
  3. 逐项做就绪检查：合同过 taskcontract_gate、PLAN 过 plan-first-gate、
     preflight 有记录、大管家 verdict 通过、plan_approvals 有该 slug 的批准、
     decision-code-gate 通过、中央队列无同 slug 的 READY/CLAIMED 行；
  4. 全过才调 dispatch-preflight-check.py 再调 dispatch_task.py 派发；
     任一不过即记 WAITING + 缺口，不派发、不写 pending 垃圾。
  退出码：0=评估完成（含全部 WAITING，属正常等待态）；1=用法错误；2=脚本异常。

硬边界（绝不做）：
  - 不伪造大管家 verdict、不伪造董事长 plan 批准（plan_approvals 只读；
    plan_id 在合同里写 PENDING-CHAIRMAN-APPROVAL，待董事长 hubctl approve
    后由本脚本下一轮自动派发）；
  - 不碰 VNC、Case C、生成式 llm_doc 工人、gate-svc SQL、儿童 CI；
  - 不直接写 central_tasks（只走 dispatch_task.py 单一入口）。

用法：
  python3 scripts/internal-iteration-enqueue.py [--dry-run]
    --dry-run 准备合同/PLAN/台账并打印就绪报告，不调 dispatch_task.py 派发。
  台账：state/internal-iteration-20261112/enqueue-ledger.jsonl（只追加）。
  cron：每周日 04:00（见 crontab）。
"""
import argparse
import glob
import json
import os
import subprocess
import sys

BASE = "/home/ubuntu/ai-team"
SCRIPTS = os.path.join(BASE, "scripts")
ITEM_DIR = os.path.join(BASE, "state", "internal-iteration-20261112")
LEDGER = os.path.join(ITEM_DIR, "enqueue-ledger.jsonl")
PREFLIGHT_LEDGER = os.path.join(BASE, "state", "preflight-ledger.jsonl")
DSN_FILE = os.path.join(BASE, "state", ".cq-dsn")
PLAN_PENDING = "PENDING-CHAIRMAN-APPROVAL"

P1_SLUG = "g5-planid-dispatch-fix-20261111"
P1_ROW = "8934d884-2c44-4ca1-b795-72014d891230"

ITEMS = {
    "p2": {
        "slug": "iter-p2-m5-diagnose-20261112",
        "label": "P2 m5 验收窗口复查与诊断",
        "evidence": "最新 m5 窗口 20261112-2035：2/6 通过（item1/3 过；item2/4/5 不通过；item6 缺数据）。item4：5 项 pending、最老 106.8 小时、默认动作无字段/无执行记录。",
        "goal": "诊断 m5 窗口 item4（主缺口）与 item2/5/6 的缺口，输出诊断报告与修复顺序，不改验收脚本本身。",
        "constraint": "只读诊断：重跑窗口验收脚本、只读队列与决策队列；不改 m5 规则、不改验收阈值。",
        "stop_when": "诊断报告含每项缺口的证据、根因判断与修复顺序，并通过自测。",
        "must_not": "不改验收规则、不伪造窗口通过、不动 gate-svc、不对外发布、不花钱。",
        "report": "诊断报告 state/internal-iteration-20261112/iter-p2-m5-diagnose-20261112-diagnosis.md，结论先行。",
        "searched": "GitHub（关键词 multi-agent task queue acceptance window monitoring）+ 枢纽 state/m5-20261105/window-*.json、queue-depth、state/decision-queue.json",
        "found": "无现成适用：GitHub 搜到的均为 agent 队列看板/可观测性工具（claudedash 等），无 m5 验收窗口诊断工具；枢纽最新窗口 20261112-2035 为 2/6，item4 是主缺口",
        "decision": "造：无现成（内部验收机制无外部对应工具），用现有窗口验收脚本 + 只读查询做诊断，不造新验收框架",
    },
    "p4": {
        "slug": "iter-p4-gate-receipt-verify-20261112",
        "label": "P4 gate 回执/插件收尾核查",
        "evidence": "/search 回执同源缺陷已于 2026-11-11 修复（memory 与 kb 各算各的 result_hash，探针验收两 hash 不同）；gate 插件放行清单（只放行 arch-note-append.sh）是否为最终状态待核查。",
        "goal": "核查 /search 回执修复是否仍成立、gate 插件放行清单是否为最终状态；只诊断、不改插件，有残留阻塞则出安全修复方案。",
        "constraint": "只读核查 + live /search 探针；插件是安全件，禁止盲改。",
        "stop_when": "诊断报告含回执探针结果与插件放行清单结论，并通过自测。",
        "must_not": "不改 gate 插件、不改 gate-svc、不伪造探针结果、不对外发布、不花钱。",
        "report": "诊断报告 state/internal-iteration-20261112/iter-p4-gate-receipt-verify-20261112-diagnosis.md，结论先行。",
        "searched": "GitHub（关键词 HMAC receipt verification gate service）+ 枢纽 scripts/gate-svc.py _handle_search 源码、ceo-seat gate.js、hubctl health",
        "found": "无现成适用：无 gate 回执核查现成工具；枢纽源码中回执修复在位，插件放行清单待核对",
        "decision": "造：无现成（回执模型为本枢纽自研门禁），用只读核查 + live /search 探针诊断，不改插件",
    },
    "p5": {
        "slug": "iter-p5-intel-mtime-writer-20261112",
        "label": "P5 intel mtime 写者定位",
        "evidence": "2026-11-12 21:11:10→21:14:13，knowledge/intel-daily-20261112/brief.md 与 draft.md 的 mtime 被重写、内容 sha 不变（126f66a7…/98cdecdd…），写者仍未定位。",
        "goal": "定位每 ~60 秒级重写 intel-daily 文件 mtime（内容不变）的写者进程，输出进程级证据，不改任何定时器/脚本。",
        "constraint": "只读探针：stat 采样、ps/crontab/lsof、audit/fanotify 盯守；定位前不动任何东西。",
        "stop_when": "诊断报告含写者进程级证据（进程名/PID/触发路径）或明确的复测结论，并通过自测。",
        "must_not": "不改定时器、不改脚本、不杀进程、不对外发布、不花钱。",
        "report": "诊断报告 state/internal-iteration-20261112/iter-p5-intel-mtime-writer-20261112-diagnosis.md，结论先行。",
        "searched": "GitHub（关键词 inotify-tools file modification monitor）+ 枢纽 knowledge/intel-daily mtime 两次采样、cron/ps/lsof",
        "found": "inotify-tools（GitHub inotify-tools/inotify-tools）：只报文件事件、不报写者进程，不适用本案；枢纽 mtime 21:11:10→21:14:13 被重写、内容 sha 不变",
        "decision": "造：无现成（通用 inotify 不给进程级写者，须针对性探针定位本枢纽写者），先用只读探针，不改定时器/脚本",
    },
}

FORBIDDEN = ["对外发布", "花钱", "删数据", "不可逆", "贴密钥原文"]
CODE_FILES = ["scripts/internal-iteration-enqueue.py", "scripts/iter-diagnose-20261112.py"]


def _ts():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def _run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as exc:
        return -1, "run failed: %s" % exc


def _ledger_append(rec):
    os.makedirs(ITEM_DIR, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def build_contract(item):
    slug = item["slug"]
    diag = "state/internal-iteration-20261112/%s-diagnosis.md" % slug
    probe = {"p2": "p2", "p4": "p4", "p5": "p5"}[
        "p2" if "p2" in slug else ("p4" if "p4" in slug else "p5")]
    return {
        "task_id": slug,
        "task_type": "script",
        "lane": "infra",
        "input": {"command": "python3 scripts/iter-diagnose-20261112.py --item %s" % probe},
        "output_path": diag,
        "evidence": "诊断报告 %s；%s" % (diag, item["evidence"]),
        "pass_line": "诊断报告已生成、非空、含“结论”一节；探针 --selftest 通过。",
        "fail_action": "RETRY_WAIT, retry_count+1; never DONE; no manual DONE",
        "verifier": "c2-verifier (deterministic)",
        "forbidden": FORBIDDEN,
        "five_part": {
            "goal": item["goal"],
            "constraint": item["constraint"],
            "stop_when": item["stop_when"],
            "must_not": item["must_not"],
            "report": item["report"],
        },
        "test_command": "python3 scripts/iter-diagnose-20261112.py --selftest",
        "test_baseline_seconds": 30,
        "estimated_seconds": 900,
        "plan_id": PLAN_PENDING,
        "plan_note": "plan_id 待董事长批准（hubctl approve %s 后由本脚本下一轮自动派发）；占位串非 UUID，门禁会如实拒绝，禁止冒用其他任务的 plan_id。" % slug,
        "code_files": CODE_FILES,
        "evidence_files": ["state/internal-iteration-20261112/enqueue-ledger.jsonl"],
    }


PLAN_TEMPLATE = """# PLAN-{slug}

## 背景
{evidence}
来源：基线 state/baselines/baseline-20261111.md §4（下一轮统一迭代修复清单）；董事长 2026-11-11 17:55 迭代双轨令。

## 目标
{goal}

## 不做
- 不碰 VNC、Case C、生成式 llm_doc 工人、gate-svc SQL 拼接、儿童 CI。
- 不改验收规则/门禁插件/定时器（诊断先行，有方案再动手）。
- 不对外发布、不花钱、不删数据、不可逆操作一律不做。

## 方案选项
- A（采用）：script 任务走中央队列，执行体为已有探针 scripts/iter-diagnose-20261112.py（--item {probe}），只读取证 + 写诊断报告，c2-verifier 验收。
- B（备选）：人工直接诊断。缺点：无队列记录、无复核、不可复现。
- C（不做）：跳过诊断直接改代码。否决：违反“方案先行、诊断先行”，且 P4 插件是安全件。

## 评估标准
- 合同：task_type=script，八字段 + 五段式齐全；test_command 为探针 --selftest，test_baseline_seconds=30。
- 验收：诊断报告非空、含“结论”一节；探针 --selftest 通过；c2-verifier 确认产物 sha/bytes。
- 门禁：dispatch 七道门全过（合同、决策码、预检、M5 开关、PLAN+合同+verdict+预检、时长、plan 存在）。

## 风险
- 诊断结论可能指向需董事长拍板的修复（如改插件、改验收规则），届时停在方案，不擅自执行。
- 探针 live 部分（如 hubctl health）失败时只记失败，不编造通过。

## 回滚
- 本任务只读诊断、无生产写操作，无需回滚；队列任务如误派，在 READY 未认领前由派发方按中央队列流程隔离，禁止手动改库。

## 需拍板点
- 董事长批准本任务的 plan（hubctl approve {slug}），否则合同 plan_id 占位，门禁拒绝派发。
- 大管家评审结论（verdict 通过/有条件通过）缺失时同样等待，不伪造。
"""


def ensure_plan(item):
    slug = item["slug"]
    probe = "p2" if "p2" in slug else ("p4" if "p4" in slug else "p5")
    d = os.path.join(BASE, "state", slug)
    path = os.path.join(d, "PLAN-%s.md" % slug)
    if os.path.exists(path):
        return path, False
    os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(PLAN_TEMPLATE.format(slug=slug, evidence=item["evidence"],
                                      goal=item["goal"], probe=probe))
    return path, True


FORMAL_DIR = os.path.join(BASE, "state", "pipeline-formal-20261106")


def ensure_contract(item):
    slug = item["slug"]
    path = os.path.join(ITEM_DIR, "%s-contract.json" % slug)
    # dispatch-gate-hub 只在 state/pipeline-formal-20261106/ 里找合同
    # （glob *<slug>*contract*.json），故同内容再放一份，文件名含 slug。
    formal_path = os.path.join(FORMAL_DIR, "%s-contract.json" % slug)
    contract = build_contract(item)
    if os.path.exists(path):
        try:
            old = json.load(open(path, encoding="utf-8"))
        except ValueError:
            old = None
        if old == contract:
            if not os.path.exists(formal_path):
                os.makedirs(FORMAL_DIR, exist_ok=True)
                with open(formal_path, "w", encoding="utf-8") as fh:
                    json.dump(contract, fh, ensure_ascii=False, indent=2)
            return path, False, contract
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(contract, fh, ensure_ascii=False, indent=2)
        os.makedirs(FORMAL_DIR, exist_ok=True)
        with open(formal_path, "w", encoding="utf-8") as fh:
            json.dump(contract, fh, ensure_ascii=False, indent=2)
        return path, True, contract
    os.makedirs(ITEM_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(contract, fh, ensure_ascii=False, indent=2)
    os.makedirs(FORMAL_DIR, exist_ok=True)
    with open(formal_path, "w", encoding="utf-8") as fh:
        json.dump(contract, fh, ensure_ascii=False, indent=2)
    return path, True, contract


def _db():
    import psycopg
    dsn = ""
    for line in open(DSN_FILE, encoding="utf-8"):
        line = line.strip()
        if line.startswith("CQ_DSN="):
            dsn = line[len("CQ_DSN="):].strip("\"'")
    return psycopg.connect(dsn)


def check_p1():
    """只读复核 P1：已关单，READY 复现即告警。返回 (state, detail)。"""
    try:
        with _db() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT status FROM central_tasks WHERE task_id::text LIKE '8934d884%%'"
            )
            rows = cur.fetchall()
    except Exception as exc:
        return "UNKNOWN", "queue read failed: %s" % exc
    if not rows:
        return "UNKNOWN", "P1 row 8934d884 not found"
    st = rows[0][0]
    if st == "QUARANTINED":
        return "SKIP-CLOSED", "P1 %s is QUARANTINED (closed 2026-11-12 21:09 CST)" % P1_SLUG
    return "ALERT", "P1 %s is %s (expected QUARANTINED)" % (P1_SLUG, st)


def check_verdict(slug):
    for d in (os.path.join(BASE, "ceo-seat", "state", "butler-verdicts"),
              os.path.join(BASE, "state", "butler-verdicts")):
        p = os.path.join(d, "%s.json" % slug)
        try:
            v = json.load(open(p, encoding="utf-8")).get("verdict")
        except (OSError, ValueError):
            continue
        if v in ("通过", "有条件通过"):
            return True, "verdict=%s (%s)" % (v, p)
        return False, "verdict=%s (%s)" % (v, p)
    return False, "no verdict file"


def check_plan_approval(slug):
    try:
        with _db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT plan_id FROM plan_approvals WHERE task_slug = %s", (slug,))
            rows = cur.fetchall()
    except Exception as exc:
        return False, "plan_approvals read failed: %s" % exc
    if rows:
        return True, "plan_id=%s" % rows[0][0]
    return False, "no plan_approvals row for %s (needs chairman hubctl approve)" % slug


def check_queued(slug):
    try:
        with _db() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT task_id, status FROM central_tasks "
                "WHERE (title = %s OR payload->'contract'->>'task_id' = %s) "
                "AND status IN ('READY','CLAIMED')", (slug, slug))
            rows = cur.fetchall()
    except Exception as exc:
        return None, "queue read failed: %s" % exc
    return rows, ""


def check_preflight(slug):
    rc, out = _run(["python3", os.path.join(SCRIPTS, "preflight-check.py"),
                    "--ledger", PREFLIGHT_LEDGER, "check", "--task", slug])
    return rc == 0, out.splitlines()[-1] if out else ""


def log_preflight(item):
    rc, out = _run(["python3", os.path.join(SCRIPTS, "preflight-check.py"),
                    "--ledger", PREFLIGHT_LEDGER, "log",
                    "--task", item["slug"], "--searched", item["searched"],
                    "--found", item["found"], "--decision", item["decision"],
                    "--kind", "code"])
    return rc == 0, out


def readiness(slug, contract_path, plan_path):
    missing = []
    rc, out = _run(["python3", os.path.join(SCRIPTS, "taskcontract_gate.py"),
                    "--insert-guard", contract_path])
    if rc != 0:
        missing.append("contract-gate: %s" % (out.splitlines()[-1][:200] if out else "refused"))
    rc, out = _run(["python3", os.path.join(SCRIPTS, "plan-first-gate.py"),
                    "check-plan", plan_path])
    if rc != 0:
        missing.append("plan-gate: %s" % (out.splitlines()[-1][:200] if out else "refused"))
    ok, detail = check_preflight(slug)
    if not ok:
        missing.append("preflight: %s" % detail[:160])
    ok, detail = check_verdict(slug)
    if not ok:
        missing.append("verdict: %s" % detail[:160])
    ok, detail = check_plan_approval(slug)
    if not ok:
        missing.append("plan-approval: %s" % detail[:160])
    rows, err = check_queued(slug)
    if rows is None:
        missing.append("queue-check: %s" % err[:160])
    elif rows:
        missing.append("already-queued: %s" % rows)
    rc, out = _run(["python3", os.path.join(SCRIPTS, "decision-code-gate.py"),
                    "--decision", slug,
                    "--code-files", ",".join(os.path.join(BASE, f) for f in CODE_FILES),
                    "--evidence", LEDGER])
    if rc != 0:
        missing.append("decision-code-gate: %s" % (out.splitlines()[-1][:200] if out else "refused"))
    return missing


def main():
    ap = argparse.ArgumentParser(description="基线下一轮内部迭代自动排队")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    dry_run = args.dry_run
    os.makedirs(ITEM_DIR, exist_ok=True)
    print("== internal-iteration-enqueue %s %s ==" % (_ts(), "(dry-run)" if dry_run else ""))

    # P1：只读复核，不排
    st, detail = check_p1()
    print("P1 %s: %s" % (st, detail))
    _ledger_append({"ts": _ts(), "slug": P1_SLUG, "item": "p1",
                    "status": st, "detail": detail})

    for key in ("p2", "p4", "p5"):
        item = ITEMS[key]
        slug = item["slug"]
        plan_path, plan_new = ensure_plan(item)
        contract_path, contract_new, _c = ensure_contract(item)
        print("%s: plan=%s%s contract=%s%s" % (
            slug, plan_path, " (new)" if plan_new else "",
            contract_path, " (new/updated)" if contract_new else ""))
        ok, detail = check_preflight(slug)
        if not ok:
            ok2, out2 = log_preflight(item)
            print("  preflight log: %s %s" % ("OK" if ok2 else "FAIL", (out2 or "")[:160]))
        missing = readiness(slug, contract_path, plan_path)
        if missing:
            print("  WAITING (%d):" % len(missing))
            for m in missing:
                print("    - %s" % m)
            _ledger_append({"ts": _ts(), "slug": slug, "item": key,
                            "status": "WAITING", "missing": missing})
            continue
        print("  READY: all gates pass, dispatching...")
        if dry_run:
            print("  (dry-run: dispatch skipped)")
            continue
        rc, out = _run(["python3", os.path.join(SCRIPTS, "dispatch-preflight-check.py"),
                        "--contract", contract_path, "--base", BASE])
        if rc != 0:
            print("  DISPATCH-PREFLIGHT REFUSED: %s" % out[:300])
            _ledger_append({"ts": _ts(), "slug": slug, "item": key,
                            "status": "PREFLIGHT-REFUSED", "detail": out[:500]})
            continue
        rc, out = _run(["python3", os.path.join(SCRIPTS, "dispatch_task.py"),
                        "--contract", contract_path])
        print("  dispatch_task rc=%s: %s" % (rc, out[:300]))
        _ledger_append({"ts": _ts(), "slug": slug, "item": key,
                        "status": "DISPATCHED" if rc == 0 else "DISPATCH-FAILED",
                        "detail": out[:500]})
    print("== done ==")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("ENQUEUE ERROR: %s" % exc)
        sys.exit(2)
