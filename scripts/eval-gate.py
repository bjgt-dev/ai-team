#!/usr/bin/env python3
"""eval-gate.py — 评测验收 SOP 门禁（SOP v1.0，2026-11-10 董事长令）。

阶段状态机：evalset -> baseline -> change -> regression -> compare -> publish。
发布分三档 smoke -> regression -> full，顺序不可跳。
唯一放行口径：口头“好了”不算数，state 文件说了算。
违规一律 exit 1，打印 VIOLATION，绝不静默放行。

用法：
  init --job-id <id> --workdir <dir>
  advance --state <state.json> --stage <阶段> --actor <席位> --artifact <文件>
          [--change-type prompt|model|workflow] [--tier smoke|regression|full]
  calibrate --state <state.json> --artifact <校准记录文件>
  record-failure --state <state.json> --case-id <id>
  register-failure --state <state.json> --case-id <id>
  check --state <state.json>              # 全量校验
  status --state <state.json>             # 阶段表
  issue-verdict --contract <合同> --eval-id <EVAL-xxx> --eval-run-id <run>
          --plan-id <uuid> [--base /home/ubuntu/ai-team]
          # G1：按模板 eval-verdict-template-20261111 §2 逐项核对 P1–P6，
          # 全过才原子写盘 verdict（只写 ceo-seat 目录），任一不过即拒绝、不写文件
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

STAGES = ["evalset", "baseline", "change", "regression", "compare", "publish"]
CHANGE_TYPES = {"prompt", "model", "workflow"}
SCORING_TYPES = {"assertion", "llm_judge", "human"}
TIERS = ["smoke", "regression", "full"]


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fail(msg):
    print(f"VIOLATION: {msg}", file=sys.stderr)
    sys.exit(1)


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: 读 state 失败 {path}: {e}", file=sys.stderr)
        sys.exit(2)


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def base_dir(state_path):
    return os.path.dirname(os.path.abspath(state_path))


def resolve(state_path, artifact):
    if os.path.isabs(artifact):
        return artifact
    return os.path.join(base_dir(state_path), artifact)


def artifact_exists(state_path, artifact):
    return os.path.exists(resolve(state_path, artifact))


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_evalset(state, state_path):
    """校验评测集文件：存在、合法 JSON、带版本号、任务非空、三类评分规则齐备形式。"""
    art = state["stages"]["evalset"].get("artifact")
    if not art:
        fail("评测集未登记（evalset 阶段未完成）")
    p = resolve(state_path, art)
    if not os.path.exists(p):
        fail(f"评测集文件不存在：{art}")
    try:
        data = read_json(p)
    except (OSError, json.JSONDecodeError) as e:
        fail(f"评测集不是合法 JSON：{art}：{e}")
    version = data.get("version")
    if not version or not isinstance(version, str):
        fail(f"评测集 {art} 缺版本号 version（须带版本号）")
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        fail(f"评测集 {art} 任务列表为空（须有任务 + 预期结果 + 评分规则）")
    for t in tasks:
        if not isinstance(t, dict) or not t.get("id") or "expected" not in t:
            fail(f"评测集 {art} 有任务缺 id/expected：{t}")
        if t.get("scoring") not in SCORING_TYPES:
            fail(f"评测集任务 {t.get('id')} 评分规则须为 assertion/llm_judge/human 三类之一")
    return data


def load_scores(state, state_path, stage, label):
    """基线/回归结果文件：JSON，含 dimensions（维度名 -> 数值）。"""
    art = state["stages"][stage].get("artifact")
    if not art:
        fail(f"{label}文件未登记（{stage} 阶段未完成）")
    p = resolve(state_path, art)
    if not os.path.exists(p):
        fail(f"{label}文件不存在：{art}")
    try:
        data = read_json(p)
    except (OSError, json.JSONDecodeError) as e:
        fail(f"{label}不是合法 JSON：{art}：{e}")
    dims = data.get("dimensions")
    if not isinstance(dims, dict) or not dims:
        fail(f"{label} {art} 缺 dimensions（须逐维度记录分数）")
    for k, v in dims.items():
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            fail(f"{label} {art} 维度 {k} 分数不是数字：{v!r}")
    return data


def regression_vs_baseline(state, state_path):
    """返回退化维度列表（回归低于基线的维度）。空 = 无退化。"""
    base = load_scores(state, state_path, "baseline", "基线")
    reg = load_scores(state, state_path, "regression", "回归结果")
    bad = []
    for dim, bval in base["dimensions"].items():
        rval = reg["dimensions"].get(dim)
        if rval is None:
            bad.append(f"{dim}：回归结果缺该维度")
        elif rval < bval:
            bad.append(f"{dim}：回归 {rval} < 基线 {bval}")
    return bad


def cmd_init(args):
    os.makedirs(args.workdir, exist_ok=True)
    state_path = os.path.join(args.workdir, "eval-sop-state.json")
    if os.path.exists(state_path):
        print(f"ERROR: state 已存在 {state_path}，不许覆盖", file=sys.stderr)
        sys.exit(2)
    state = {
        "job_id": args.job_id,
        "created_at": now_iso(),
        "evalset_version": None,
        "change_type": None,
        "calibration": {"artifact": None, "ts": None},
        "publish_tier": None,
        "pending_failures": [],
        "registered_failures": [],
        "stages": {s: {"status": "pending", "actor": None,
                        "artifact": None, "ts": None}
                   for s in STAGES},
    }
    save_state(state_path, state)
    print(f"OK: job {args.job_id} 已建 {state_path}")


def validate_advance(state, state_path, stage, actor, artifact, change_type, tier):
    idx = STAGES.index(stage)
    is_republish = stage == "publish" and state["stages"]["publish"]["status"] == "done"
    # 1. 顺序：前面阶段必须全 done（publish 再次推进只校验档位递进）
    if not is_republish:
        for prev in STAGES[:idx]:
            if state["stages"][prev]["status"] != "done":
                fail(f"阶段 {stage} 之前 {prev} 未完成，不许跳步")
    # 2. 产出物必须存在
    if not artifact:
        fail(f"阶段 {stage} 缺产出物 --artifact")
    if not artifact_exists(state_path, artifact):
        fail(f"阶段 {stage} 产出物不存在：{artifact}")
    # 3. evalset：评测集文件须合法且带版本号
    if stage == "evalset":
        data = read_json(resolve(state_path, artifact)) if artifact_exists(state_path, artifact) else None
        # 用临时 state 做一次完整校验
        tmp = json.loads(json.dumps(state))
        tmp["stages"]["evalset"]["artifact"] = artifact
        load_evalset(tmp, state_path)
    # 4. baseline：须与评测集同一版本
    if stage == "baseline":
        load_evalset(state, state_path)
        data = load_scores({**state, "stages": {**state["stages"], "baseline": {"artifact": artifact}}},
                           state_path, "baseline", "基线")
        ev = load_evalset(state, state_path)
        if data.get("evalset_version") != ev.get("version"):
            fail(f"基线 evalset_version={data.get('evalset_version')} 与评测集版本 {ev.get('version')} 不一致")
    # 5. change：须声明改动类型（提示词/模型/工作流）
    if stage == "change" and change_type not in CHANGE_TYPES:
        fail("change 阶段须带 --change-type prompt|model|workflow（改提示词/模型/工作流必跑回归）")
    # 6. regression：评分器须先对人工判断校准
    if stage == "regression":
        cal = state.get("calibration") or {}
        if not cal.get("artifact") or not artifact_exists(state_path, cal["artifact"]):
            fail("评分器未校准（校准记录不存在），不许跑回归")
        load_evalset(state, state_path)
    # 7. compare：回归任一维度低于基线即拦截
    if stage == "compare":
        tmp = json.loads(json.dumps(state))
        tmp["stages"]["regression"]["artifact"] = state["stages"]["regression"]["artifact"]
        bad = regression_vs_baseline(state, state_path)
        if bad:
            fail("回归退化，不许发布：" + "；".join(bad))
    # 8. publish：无退化 + 无待登记失败 + 档位顺序 smoke->regression->full
    if stage == "publish":
        bad = regression_vs_baseline(state, state_path)
        if bad:
            fail("回归退化，不许发布：" + "；".join(bad))
        pending = state.get("pending_failures") or []
        if pending:
            fail(f"有生产失败未登记进评测集，不许发布：{pending}")
        cur = state.get("publish_tier")
        if tier not in TIERS:
            fail("publish 须带 --tier smoke|regression|full")
        if cur is None:
            if tier != "smoke":
                fail(f"发布须从 smoke 起步，不许直接发 {tier}")
        else:
            nxt = TIERS[TIERS.index(cur) + 1] if TIERS.index(cur) + 1 < len(TIERS) else None
            if tier != nxt:
                fail(f"发布档位须按 smoke->regression->full 顺序，不许从 {cur} 跳到 {tier}")


def cmd_advance(args):
    state = load_state(args.state)
    if args.stage not in STAGES:
        print(f"ERROR: 未知阶段 {args.stage}", file=sys.stderr)
        sys.exit(2)
    cur = state["stages"][args.stage]
    if cur["status"] == "done" and args.stage != "publish":
        fail(f"阶段 {args.stage} 已完成，不许重复推进")
    validate_advance(state, args.state, args.stage, args.actor,
                     args.artifact, args.change_type, args.tier)
    cur.update({"status": "done", "actor": args.actor,
                "artifact": args.artifact, "ts": now_iso()})
    if args.stage == "evalset":
        ev = load_evalset(state, args.state)
        state["evalset_version"] = ev.get("version")
    if args.stage == "change":
        state["change_type"] = args.change_type
    if args.stage == "publish":
        state["publish_tier"] = args.tier
    save_state(args.state, state)
    extra = f" tier={args.tier}" if args.stage == "publish" else ""
    print(f"OK: {args.stage} done by {args.actor}{extra}")


def cmd_calibrate(args):
    state = load_state(args.state)
    if not artifact_exists(args.state, args.artifact):
        fail(f"校准记录文件不存在：{args.artifact}")
    state["calibration"] = {"artifact": args.artifact, "ts": now_iso()}
    save_state(args.state, state)
    print(f"OK: 评分器已校准，记录 {args.artifact}")


def cmd_record_failure(args):
    state = load_state(args.state)
    if args.case_id in state["pending_failures"] or args.case_id in state["registered_failures"]:
        fail(f"失败案例 {args.case_id} 已登记/待登记，不许重复记录")
    state["pending_failures"].append(args.case_id)
    save_state(args.state, state)
    print(f"OK: 生产失败已记录待登记 {args.case_id}，须登记进评测集才许关单/发布")


def cmd_register_failure(args):
    state = load_state(args.state)
    if args.case_id not in state["pending_failures"]:
        fail(f"失败案例 {args.case_id} 不在待登记列表，须先 record-failure")
    ev = load_evalset(state, args.state)
    ev_path = resolve(args.state, state["stages"]["evalset"]["artifact"])
    with open(ev_path, encoding="utf-8") as f:
        content = f.read()
    if args.case_id not in content:
        fail(f"失败案例 {args.case_id} 未写进评测集文件，不许算登记")
    state["pending_failures"].remove(args.case_id)
    state["registered_failures"].append(args.case_id)
    save_state(args.state, state)
    print(f"OK: 失败案例 {args.case_id} 已登记进评测集")


def check_state(state, state_path):
    """全量校验：返回违规列表（空 = 通过）。"""
    problems = []
    for i, s in enumerate(STAGES):
        if state["stages"][s]["status"] == "done":
            for prev in STAGES[:i]:
                if state["stages"][prev]["status"] != "done":
                    problems.append(f"{s} 已完成但前置 {prev} 未完成")
    for s in STAGES:
        st = state["stages"][s]
        if st["status"] == "done":
            if not st.get("artifact") or not artifact_exists(state_path, st["artifact"]):
                problems.append(f"{s} 产出物缺失：{st.get('artifact')}")
    # 评测集合法性
    if state["stages"]["evalset"]["status"] == "done":
        try:
            ev = load_evalset(state, state_path)
            if state.get("evalset_version") and ev.get("version") != state["evalset_version"]:
                problems.append("评测集版本与 state 记录不一致（疑似手工篡改）")
        except SystemExit:
            problems.append("评测集文件不合法/缺版本号/缺任务/评分规则非法")
    # 基线版本一致
    if state["stages"]["baseline"]["status"] == "done":
        try:
            base = load_scores(state, state_path, "baseline", "基线")
            ev = load_evalset(state, state_path)
            if base.get("evalset_version") != ev.get("version"):
                problems.append("基线 evalset_version 与评测集版本不一致")
        except SystemExit:
            problems.append("基线文件不合法/缺 dimensions")
    # 校准
    if state["stages"]["regression"]["status"] == "done":
        cal = state.get("calibration") or {}
        if not cal.get("artifact") or not artifact_exists(state_path, cal["artifact"]):
            problems.append("回归已跑但校准记录不存在")
    # 退化
    if state["stages"]["regression"]["status"] == "done" and state["stages"]["baseline"]["status"] == "done":
        try:
            bad = regression_vs_baseline(state, state_path)
            if bad:
                problems.append("回归退化：" + "；".join(bad))
        except SystemExit:
            problems.append("回归/基线文件不合法，无法对比")
    # 失败登记
    if state["stages"]["publish"]["status"] == "done" and state.get("pending_failures"):
        problems.append(f"已发布但有失败案例未登记：{state['pending_failures']}")
    # 已登记失败须在评测集文件里
    if state.get("registered_failures") and state["stages"]["evalset"]["status"] == "done":
        ev_path = resolve(state_path, state["stages"]["evalset"]["artifact"])
        try:
            with open(ev_path, encoding="utf-8") as f:
                content = f.read()
            for cid in state["registered_failures"]:
                if cid not in content:
                    problems.append(f"已登记失败 {cid} 不在评测集文件里")
        except OSError:
            problems.append("评测集文件读不到，无法核对已登记失败")
    return problems


def cmd_check(args):
    state = load_state(args.state)
    problems = check_state(state, args.state)
    if problems:
        for p in problems:
            print(f"VIOLATION: {p}", file=sys.stderr)
        sys.exit(1)
    print("OK: 全量校验通过")


def cmd_status(args):
    state = load_state(args.state)
    print(f"job={state['job_id']} evalset_version={state.get('evalset_version')} "
          f"change_type={state.get('change_type')} publish_tier={state.get('publish_tier')} "
          f"calibrated={bool((state.get('calibration') or {}).get('artifact'))} "
          f"pending_failures={state.get('pending_failures')} "
          f"registered_failures={state.get('registered_failures')}")
    for s in STAGES:
        st = state["stages"][s]
        mark = "DONE " if st["status"] == "done" else ".... "
        extra = f" by {st['actor']}" if st["actor"] else ""
        print(f"  {mark}{s}{extra}")


# ---- eval verdict 签发（G1，模板 eval-verdict-template-20261111 §2/§7）----
EVAL_IDS = ("EVAL-001", "EVAL-003", "EVAL-005")
EVAL_TEMPLATE = "eval-verdict-template-20261111"
EVAL_TEMPLATE_VERSION = 1
EVAL_TEMPLATE_EXPIRY = "2026-12-11"  # 模板确认后 30 天上限
EVAL_PLAN_SLUG = "eval-os-mvp-phase2"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
RUN_RE = re.compile(r"^eval-run-\d{8}-\d+$")


def _canonical_hash(obj):
    """合同 canonical hash，与 dispatch_task.py _canonical_hash 一字一致。"""
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _sql_quote(s):
    return "'" + str(s).replace("'", "''") + "'"


def db_query(sql):
    """管理通道：docker exec 进 ops-pg（与 G8 导入同一通道）。返回 (rc, stdout)。"""
    r = subprocess.run(["docker", "exec", "-i", "-u", "postgres", "ops-pg",
                        "psql", "-d", "central_queue", "-t", "-A", "-c", sql],
                       capture_output=True, text=True, timeout=60)
    return r.returncode, (r.stdout + r.stderr).strip()


def _utc_z(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_issue_verdict(args):
    base = args.base
    eval_id, run_id, plan_id = args.eval_id, args.eval_run_id, args.plan_id
    # P5 先行：格式不对直接拒（不碰 DB、不读合同）
    if eval_id not in EVAL_IDS:
        fail("eval_id %s 超出模板 §1 范围（只认 EVAL-001/EVAL-003/EVAL-005）" % eval_id)
    if not RUN_RE.match(run_id or ""):
        fail("eval_run_id %s 格式不对（须 eval-run-<YYYYMMDD>-<序号>）" % run_id)
    if not UUID_RE.match(plan_id or ""):
        fail("plan_id 不是 UUID 格式")
    # 读合同
    try:
        contract = read_json(args.contract)
    except (OSError, json.JSONDecodeError) as e:
        fail("合同读不到/不是合法 JSON：%s：%s" % (args.contract, e))
    if not isinstance(contract, dict):
        fail("合同不是 JSON 对象")
    task_slug = contract.get("task_id")
    if not task_slug or not isinstance(task_slug, str):
        fail("合同缺 task_id（task_slug）")
    # P1：模板已生效
    tpl_path = os.path.join(base, "ceo-seat", "state", "butler-verdicts",
                            EVAL_TEMPLATE + ".md")
    try:
        with open(tpl_path, encoding="utf-8") as f:
            tpl_text = f.read()
    except OSError:
        fail("模板文件不在定稿位置：%s" % tpl_path)
    if "状态：已生效" not in tpl_text:
        fail("模板未生效（无“状态：已生效”记录），不得签发")
    if datetime.now(timezone.utc).strftime("%Y-%m-%d") > EVAL_TEMPLATE_EXPIRY:
        fail("模板已过有效期上限 %s，不得签发" % EVAL_TEMPLATE_EXPIRY)
    # P2：二阶段批准 24h 内有效
    rc, out = db_query(
        "SELECT 1 FROM plan_approvals WHERE task_slug=%s AND plan_id=%s::uuid"
        " AND ts > now() - interval '24 hours' LIMIT 1;"
        % (_sql_quote(EVAL_PLAN_SLUG), _sql_quote(plan_id)))
    if rc != 0 or out.strip() != "1":
        fail("P2 不过：plan_approvals 无 %s 的有效行（plan_id 或已过期）" % EVAL_PLAN_SLUG)
    # P3：四张 eval 表存在
    rc, out = db_query(
        "SELECT count(*) FROM (VALUES ('eval_cases'),('eval_traces'),"
        "('eval_failures'),('eval_baselines')) AS t(n) WHERE to_regclass(t.n) IS NOT NULL;")
    if rc != 0 or out.strip() != "4":
        fail("P3 不过：四张 eval 表不全（migration 009 未落地）")
    # P4：合同过 insert-guard + 绑定一致
    gate = os.path.join(base, "scripts", "taskcontract_gate.py")
    if not os.path.exists(gate):
        fail("P4 无法核对：taskcontract_gate.py 不存在")
    r = subprocess.run(["python3", gate, "--insert-guard", args.contract],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        detail = (r.stdout + r.stderr).strip()
        fail("P4 不过：合同未过 taskcontract_gate --insert-guard：%s"
             % (detail.splitlines()[-1] if detail else "无输出"))
    if contract.get("eval_id") != eval_id or contract.get("eval_run_id") != run_id:
        fail("P4 不过：合同顶层 eval_id/eval_run_id 与签发参数不一致（G2 门禁必 403）")
    contract_hash = _canonical_hash(contract)
    # P6：同一（run，eval）尚未签发；目标文件不存在
    vdir = os.path.join(base, "ceo-seat", "state", "butler-verdicts")
    target = os.path.join(vdir, "%s.json" % task_slug)
    if os.path.exists(target):
        fail("P6 不过：verdict 文件已存在 %s，不得覆盖、不得重签" % target)
    if os.path.isdir(vdir):
        for fn in sorted(os.listdir(vdir)):
            if not fn.endswith(".json"):
                continue
            try:
                dd = read_json(os.path.join(vdir, fn))
            except (OSError, json.JSONDecodeError):
                continue
            if dd.get("eval") is True and dd.get("eval_run_id") == run_id \
                    and dd.get("eval_id") == eval_id:
                fail("P6 不过：同一（%s，%s）已有签发文件 %s，不得重签" % (run_id, eval_id, fn))
    # 全过：写盘（原子 tmp + os.replace，与 save_state 同款）
    issued = datetime.now(timezone.utc)
    valid_until = issued + timedelta(hours=24)
    verdict = {
        "task_id": task_slug,
        "eval": True,
        "verdict": "eval-通过",
        "eval_id": eval_id,
        "eval_run_id": run_id,
        "plan_id": plan_id,
        "contract_hash": contract_hash,
        "conditions": [],
        "template": EVAL_TEMPLATE,
        "template_version": EVAL_TEMPLATE_VERSION,
        "issued_by": "eval-gate advance",
        "issued_at": _utc_z(issued),
        "valid_until": _utc_z(valid_until),
        "scope": "仅限本次 run 的 %s 派发一次" % eval_id,
        "reason": "P1–P6 全过，合同校验通过，plan_id 有效",
    }
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(verdict, f, ensure_ascii=False, indent=2)
    os.replace(tmp, target)
    # 记录 ②：Trace 事件 VERDICT_ISSUED（trace_id 为签发前占位值，见实施日志 §0；
    # event_id 是 GENERATED ALWAYS 标识列，不得显式写值，由库自动生成——
    # 2026-11-11 Case A 首签实测：显式 nextval 即报 cannot insert a non-DEFAULT value）
    trace_id = "verdict:%s:%s" % (run_id, eval_id)
    sql = ("INSERT INTO eval_traces (trace_id, eval_run_id, task_id, eval_id,"
           " parent_event_id, actor, event_type, status, input_summary, output_summary,"
           " latency_ms, ts) VALUES (%s, %s, NULL, %s,"
           " NULL, 'eval-gate', 'VERDICT_ISSUED', 'READY', %s, %s, NULL, now());"
           % (_sql_quote(trace_id), _sql_quote(run_id), _sql_quote(eval_id),
              _sql_quote("contract=%s plan_id=%s" % (os.path.basename(args.contract), plan_id)),
              _sql_quote("verdict=%s valid_until=%s" % (target, _utc_z(valid_until)))))
    rc, out = db_query(sql)
    trace_ok = rc == 0
    trace_err = "" if trace_ok else out.strip()[:300]
    # 记录 ①：签发日志行
    rec = {"ts": _utc_z(issued), "eval_id": eval_id, "eval_run_id": run_id,
           "task_slug": task_slug, "verdict_path": target,
           "contract_hash": contract_hash, "plan_id": plan_id,
           "trace_id": trace_id, "trace_ok": trace_ok}
    if not trace_ok:
        rec["trace_error"] = trace_err
    log_path = os.path.join(vdir, "eval-verdict-issue-log.jsonl")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("OK: verdict 已签发 %s" % target)
    if trace_ok:
        print("OK: VERDICT_ISSUED 事件已写 eval_traces（trace_id=%s）" % trace_id)
    else:
        print("WARNING: verdict 已落盘有效，但 VERDICT_ISSUED 写库失败：%s；须按日志行补写事件"
              % trace_err, file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="评测验收 SOP 门禁")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--job-id", required=True)
    p.add_argument("--workdir", required=True)
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("advance")
    p.add_argument("--state", required=True)
    p.add_argument("--stage", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--artifact", required=True)
    p.add_argument("--change-type", default=None)
    p.add_argument("--tier", default=None)
    p.set_defaults(fn=cmd_advance)
    p = sub.add_parser("calibrate")
    p.add_argument("--state", required=True)
    p.add_argument("--artifact", required=True)
    p.set_defaults(fn=cmd_calibrate)
    p = sub.add_parser("record-failure")
    p.add_argument("--state", required=True)
    p.add_argument("--case-id", required=True)
    p.set_defaults(fn=cmd_record_failure)
    p = sub.add_parser("register-failure")
    p.add_argument("--state", required=True)
    p.add_argument("--case-id", required=True)
    p.set_defaults(fn=cmd_register_failure)
    p = sub.add_parser("check")
    p.add_argument("--state", required=True)
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("status")
    p.add_argument("--state", required=True)
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("issue-verdict")
    p.add_argument("--contract", required=True)
    p.add_argument("--eval-id", required=True)
    p.add_argument("--eval-run-id", required=True)
    p.add_argument("--plan-id", required=True)
    p.add_argument("--base", default="/home/ubuntu/ai-team")
    p.set_defaults(fn=cmd_issue_verdict)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
