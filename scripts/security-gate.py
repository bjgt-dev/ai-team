#!/usr/bin/env python3
"""security-gate.py — 安全权限 SOP 门禁（SOP v1.0，2026-11-10 董事长令）。

阶段状态机：register -> grant -> execute -> audit -> revoke（或 revoke-drill）。
执行层强制校验：身份已登记、token 未过期、工具在白名单内、高影响动作须有
未消费的审批单（消费后标记已用，防重放）、审计日志条目存在。
唯一放行口径：口头“已授权”不算数，state 文件 + 审计日志说了算。
违规一律 exit 1，打印 VIOLATION，绝不静默放行。
本脚本不产生、不输出、不记录任何真实密钥/token 值；--token 只接受占位 ID。

用法：
  init --job-id <id> --workdir <dir>
  advance --state <state.json> --stage register --agent <agent> \
      --owner <负责人> --purpose <用途> --expires-at <ISO 时间>
  advance --state <state.json> --stage grant --agent <agent> --tool <工具> \
      --token <token 占位 ID> --expires-at <ISO 时间>
  approve --state <state.json> --approval-id <id> --agent <agent> --action <动作>
  advance --state <state.json> --stage execute --agent <agent> --tool <工具> \
      --token <token 占位 ID> --audit-log <日志路径> [--approval <审批单 id>] [--high-impact]
  advance --state <state.json> --stage audit --agent <agent> --audit-log <日志路径>
  advance --state <state.json> --stage revoke --agent <agent>
  advance --state <state.json> --stage revoke-drill --agent <agent>
  check --state <state.json>
  status --state <state.json>
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

STAGES = ["register", "grant", "execute", "audit", "revoke", "revoke-drill"]
# revoke 与 revoke-drill 二选一，均为终态（顺序位相同）
STAGE_IDX = {"register": 0, "grant": 1, "execute": 2, "audit": 3,
             "revoke": 4, "revoke-drill": 4}
# 默认只读白名单；写/动作类须逐个授权；高影响须逐个授权 + 人工审批单
READ_TOOLS = {"read_file", "list_dir", "search_web", "read_state"}
WRITE_TOOLS = {"write_file", "exec_cmd"}
HIGH_IMPACT_TOOLS = {"deploy", "delete_file", "send_message", "spend_money"}
ALLOWED_TOOLS = READ_TOOLS | WRITE_TOOLS | HIGH_IMPACT_TOOLS


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_time(s):
    """解析 ISO 时间；失败返回 None。接受末尾 Z。"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def now_dt():
    return datetime.now(timezone.utc)


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


def audit_has_entry(log_path, agent, tool, token=None):
    """审计日志文件里是否存在该 agent + 该工具的条目（JSON 行）。"""
    try:
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("agent") == agent and e.get("tool") == tool:
                    if token is None or e.get("token") == token:
                        return True
    except OSError:
        return False
    return False


def cmd_init(args):
    os.makedirs(args.workdir, exist_ok=True)
    state_path = os.path.join(args.workdir, "security-sop-state.json")
    if os.path.exists(state_path):
        print(f"ERROR: state 已存在 {state_path}，不许覆盖", file=sys.stderr)
        sys.exit(2)
    state = {
        "job_id": args.job_id,
        "created_at": now_iso(),
        "stages": {s: {"status": "pending", "actor": None, "ts": None}
                   for s in STAGES},
        "identities": {},
        "grants": [],
        "tokens": {},
        "approvals": {},
        "executions": [],
        "audit_log_path": None,
    }
    save_state(state_path, state)
    print(f"OK: job {args.job_id} 已建 {state_path}")


def check_order(state, stage):
    """顺序：前置阶段必须全 done；revoke/revoke-drill 二选一，互斥。"""
    idx = STAGE_IDX[stage]
    for s, i in STAGE_IDX.items():
        if i < idx and state["stages"][s]["status"] != "done":
            # grant/execute 为可重复阶段，done 即算完成
            fail(f"阶段 {stage} 之前 {s} 未完成，不许跳步")
    if stage in ("revoke", "revoke-drill"):
        other = "revoke-drill" if stage == "revoke" else "revoke"
        if state["stages"][other]["status"] == "done":
            fail(f"{other} 已是终态，不许再进 {stage}")


def check_identity_active(state, agent):
    ident = state["identities"].get(agent)
    if ident is None:
        fail(f"身份 {agent} 未登记，不许执行")
    if ident.get("status") != "active":
        fail(f"身份 {agent} 已停用（status={ident.get('status')}），调用必拒")
    exp = parse_time(ident.get("expires_at"))
    if exp is None:
        fail(f"身份 {agent} 有效期格式无效")
    if exp <= now_dt():
        fail(f"身份 {agent} 已过期（expires_at={ident.get('expires_at')}），调用必拒")
    return ident


def cmd_approve(args):
    state = load_state(args.state)
    if args.approval_id in state["approvals"]:
        print(f"ERROR: 审批单 {args.approval_id} 已存在，不许覆盖", file=sys.stderr)
        sys.exit(2)
    check_identity_active(state, args.agent)
    if args.action not in HIGH_IMPACT_TOOLS:
        print(f"ERROR: 审批单只用于高影响动作，{args.action} 不是高影响工具", file=sys.stderr)
        sys.exit(2)
    state["approvals"][args.approval_id] = {
        "agent": args.agent, "action": args.action,
        "status": "unused", "ts": now_iso(),
    }
    save_state(args.state, state)
    print(f"OK: 审批单 {args.approval_id} 已签发给 {args.agent}（{args.action}，unused）")


def validate_advance(state, state_path, args):
    stage = args.stage
    agent = args.agent
    if stage == "register":
        if not args.owner or not args.purpose or not args.expires_at:
            fail("register 须带 --owner --purpose --expires-at，四项缺一不可")
        exp = parse_time(args.expires_at)
        if exp is None:
            fail(f"register 的 --expires-at 格式无效：{args.expires_at}")
        if exp <= now_dt():
            fail("register 的有效期已过期，不许登记过期身份")
        if agent in state["identities"]:
            fail(f"身份 {agent} 已登记，不许重复登记")
    elif stage == "grant":
        check_identity_active(state, agent)
        if not args.tool or not args.token or not args.expires_at:
            fail("grant 须带 --tool --token --expires-at")
        if args.tool not in ALLOWED_TOOLS:
            fail(f"工具 {args.tool} 不在白名单内，不许签发")
        exp = parse_time(args.expires_at)
        if exp is None:
            fail(f"grant 的 --expires-at 格式无效：{args.expires_at}")
        if exp <= now_dt():
            fail(f"token {args.token} 有效期已过期，不许签发过期 token")
        if args.token in state["tokens"]:
            fail(f"token {args.token} 已签发过，不许复用占位 ID")
    elif stage == "execute":
        check_identity_active(state, agent)
        if not args.tool or not args.token or not args.audit_log:
            fail("execute 须带 --tool --token --audit-log")
        if args.tool not in ALLOWED_TOOLS:
            fail(f"工具 {args.tool} 不在白名单内，越权调用拦截")
        tok = state["tokens"].get(args.token)
        if tok is None:
            fail(f"token {args.token} 未签发，调用必拒")
        if tok.get("agent") != agent:
            fail(f"token {args.token} 不属于 {agent}，调用必拒")
        if tok.get("status") != "active":
            fail(f"token {args.token} 已停用（status={tok.get('status')}），调用必拒")
        exp = parse_time(tok.get("expires_at"))
        if exp is None or exp <= now_dt():
            fail(f"token {args.token} 已过期（expires_at={tok.get('expires_at')}），调用必拒")
        granted = [g for g in state["grants"]
                   if g["agent"] == agent and g["tool"] == args.tool
                   and g["token"] == args.token]
        if not granted:
            fail(f"工具 {args.tool} 未授权给 {agent}（token {args.token}），越权调用拦截")
        is_high = args.high_impact or args.tool in HIGH_IMPACT_TOOLS
        if is_high:
            if not args.approval:
                fail(f"高影响动作 {args.tool} 无审批单，必拒")
            ap = state["approvals"].get(args.approval)
            if ap is None:
                fail(f"审批单 {args.approval} 不存在，必拒")
            if ap.get("status") != "unused":
                fail(f"审批单 {args.approval} 已消费（status={ap.get('status')}），重放必拒")
            if ap.get("agent") != agent or ap.get("action") != args.tool:
                fail(f"审批单 {args.approval} 属主/动作与本次调用不符，必拒")
        # 审计日志条目必须存在（日志由执行层外写，gate 只校验存在）
        if not os.path.exists(args.audit_log):
            fail(f"审计日志不存在：{args.audit_log}")
        if not audit_has_entry(args.audit_log, agent, args.tool, args.token):
            fail(f"审计日志无 {agent} + {args.tool} 的条目，缺条不许执行")
    elif stage == "audit":
        if not args.audit_log:
            fail("audit 须带 --audit-log")
        if not os.path.exists(args.audit_log):
            fail(f"审计日志不存在：{args.audit_log}")
        for ex in state["executions"]:
            if not audit_has_entry(args.audit_log, ex["agent"], ex["tool"], ex["token"]):
                fail(f"审计日志缺执行记录条目：{ex['agent']} + {ex['tool']}")
        state["audit_log_path"] = args.audit_log
    elif stage in ("revoke", "revoke-drill"):
        ident = state["identities"].get(agent)
        if ident is None:
            fail(f"身份 {agent} 未登记，不许撤销")
        if ident.get("status") == "revoked":
            fail(f"身份 {agent} 已停用，不许重复撤销")


def cmd_advance(args):
    state = load_state(args.state)
    if args.stage not in STAGES:
        print(f"ERROR: 未知阶段 {args.stage}", file=sys.stderr)
        sys.exit(2)
    if not args.agent:
        print("ERROR: advance 须带 --agent", file=sys.stderr)
        sys.exit(2)
    cur = state["stages"][args.stage]
    # grant/execute 可重复推进（每工具/每调用一次）；其余阶段只许一次
    if cur["status"] == "done" and args.stage not in ("grant", "execute"):
        fail(f"阶段 {args.stage} 已完成，不许重复推进")
    check_order(state, args.stage)
    validate_advance(state, args.state, args)
    ts = now_iso()
    if args.stage == "register":
        state["identities"][args.agent] = {
            "owner": args.owner, "purpose": args.purpose,
            "expires_at": args.expires_at, "status": "active", "ts": ts,
        }
    elif args.stage == "grant":
        state["grants"].append({
            "agent": args.agent, "tool": args.tool, "token": args.token,
            "expires_at": args.expires_at, "ts": ts,
        })
        state["tokens"][args.token] = {
            "agent": args.agent, "expires_at": args.expires_at,
            "status": "active", "ts": ts,
        }
    elif args.stage == "execute":
        is_high = args.high_impact or args.tool in HIGH_IMPACT_TOOLS
        if is_high:
            state["approvals"][args.approval]["status"] = "consumed"
            state["approvals"][args.approval]["consumed_ts"] = ts
        state["executions"].append({
            "agent": args.agent, "tool": args.tool, "token": args.token,
            "approval": args.approval if is_high else None,
            "high_impact": bool(is_high), "ts": ts,
            "audit_log": args.audit_log,
        })
    elif args.stage in ("revoke", "revoke-drill"):
        state["identities"][args.agent]["status"] = "revoked"
        state["identities"][args.agent]["revoked_ts"] = ts
        for tok in state["tokens"].values():
            if tok.get("agent") == args.agent:
                tok["status"] = "revoked"
    cur.update({"status": "done", "actor": args.agent, "ts": ts})
    save_state(args.state, state)
    print(f"OK: {args.stage} done by {args.agent}")


def check_state(state, state_path):
    """全量校验：返回违规列表（空 = 通过）。"""
    problems = []
    # 顺序完整性：done 的阶段之前必须全 done（revoke/revoke-drill 二选一）
    for s, i in STAGE_IDX.items():
        if state["stages"][s]["status"] == "done":
            for prev, j in STAGE_IDX.items():
                if j < i and state["stages"][prev]["status"] != "done":
                    problems.append(f"{s} 已完成但前置 {prev} 未完成")
    if (state["stages"]["revoke"]["status"] == "done"
            and state["stages"]["revoke-drill"]["status"] == "done"):
        problems.append("revoke 与 revoke-drill 不许同时为终态")
    # 身份登记完整性
    for agent, ident in state.get("identities", {}).items():
        if not ident.get("owner") or not ident.get("purpose") or not ident.get("expires_at"):
            problems.append(f"身份 {agent} 登记不全（缺 owner/purpose/expires_at）")
        if parse_time(ident.get("expires_at")) is None:
            problems.append(f"身份 {agent} 有效期格式无效")
    # 授权：工具须在白名单内，agent 须已登记，token 须存在
    for g in state.get("grants", []):
        if g["tool"] not in ALLOWED_TOOLS:
            problems.append(f"授权工具 {g['tool']} 不在白名单内")
        if g["agent"] not in state["identities"]:
            problems.append(f"授权给未登记身份 {g['agent']}")
        if g["token"] not in state.get("tokens", {}):
            problems.append(f"授权 token {g['token']} 不存在")
    # 执行记录：身份/授权/token/审批/审计日志逐项对
    seen_approvals = []
    for ex in state.get("executions", []):
        if ex["agent"] not in state["identities"]:
            problems.append(f"执行者 {ex['agent']} 未登记")
        if ex["tool"] not in ALLOWED_TOOLS:
            problems.append(f"执行工具 {ex['tool']} 不在白名单内")
        tok = state.get("tokens", {}).get(ex["token"])
        if tok is None:
            problems.append(f"执行 token {ex['token']} 未签发")
        elif tok.get("agent") != ex["agent"]:
            problems.append(f"执行 token {ex['token']} 不属于 {ex['agent']}")
        else:
            exp = parse_time(tok.get("expires_at"))
            ex_ts = parse_time(ex.get("ts"))
            if exp is not None and ex_ts is not None and exp <= ex_ts:
                problems.append(f"执行时 token {ex['token']} 已过期")
        if not any(g["agent"] == ex["agent"] and g["tool"] == ex["tool"]
                   and g["token"] == ex["token"] for g in state.get("grants", [])):
            problems.append(f"执行 {ex['agent']} + {ex['tool']} 无对应授权")
        if ex.get("high_impact") or ex["tool"] in HIGH_IMPACT_TOOLS:
            ap_id = ex.get("approval")
            if not ap_id:
                problems.append(f"高影响执行 {ex['tool']} 无审批单")
            else:
                seen_approvals.append(ap_id)
                ap = state.get("approvals", {}).get(ap_id)
                if ap is None:
                    problems.append(f"高影响执行审批单 {ap_id} 不存在")
                elif ap.get("agent") != ex["agent"] or ap.get("action") != ex["tool"]:
                    problems.append(f"审批单 {ap_id} 属主/动作与执行不符")
        log = ex.get("audit_log") or state.get("audit_log_path")
        if not log or not os.path.exists(log):
            problems.append(f"执行 {ex['agent']} + {ex['tool']} 审计日志不存在")
        elif not audit_has_entry(log, ex["agent"], ex["tool"], ex["token"]):
            problems.append(f"执行 {ex['agent']} + {ex['tool']} 审计日志缺条目")
        ident = state["identities"].get(ex["agent"])
        if ident and ident.get("status") == "revoked":
            rev_ts = parse_time(ident.get("revoked_ts"))
            ex_ts = parse_time(ex.get("ts"))
            if rev_ts is not None and ex_ts is not None and ex_ts > rev_ts:
                problems.append(f"执行 {ex['agent']} + {ex['tool']} 发生在撤销之后")
    if len(seen_approvals) != len(set(seen_approvals)):
        problems.append("同一审批单被多次消费（重放）")
    # 审批单：已消费的必须恰好对应一次执行
    for ap_id, ap in state.get("approvals", {}).items():
        if ap.get("status") == "consumed" and seen_approvals.count(ap_id) != 1:
            problems.append(f"审批单 {ap_id} 标记已消费但无恰好一次对应执行")
    # 撤销后 token 须停用
    for agent, ident in state.get("identities", {}).items():
        if ident.get("status") == "revoked":
            for tok_id, tok in state.get("tokens", {}).items():
                if tok.get("agent") == agent and tok.get("status") == "active":
                    problems.append(f"身份 {agent} 已撤销但 token {tok_id} 仍 active")
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
    print(f"job={state['job_id']} identities={len(state.get('identities', {}))} "
          f"grants={len(state.get('grants', []))} executions={len(state.get('executions', []))}")
    for s in STAGES:
        st = state["stages"][s]
        mark = "DONE " if st["status"] == "done" else ".... "
        extra = f" by {st['actor']}" if st.get("actor") else ""
        print(f"  {mark}{s}{extra}")
    for agent, ident in state.get("identities", {}).items():
        print(f"  identity {agent}: {ident.get('status')} expires={ident.get('expires_at')}")


def main():
    ap = argparse.ArgumentParser(description="安全权限 SOP 门禁")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--job-id", required=True)
    p.add_argument("--workdir", required=True)
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("advance")
    p.add_argument("--state", required=True)
    p.add_argument("--stage", required=True)
    p.add_argument("--agent", required=True)
    p.add_argument("--owner", default="")
    p.add_argument("--purpose", default="")
    p.add_argument("--expires-at", default="")
    p.add_argument("--tool", default="")
    p.add_argument("--token", default="")
    p.add_argument("--approval", default="")
    p.add_argument("--audit-log", default="")
    p.add_argument("--high-impact", action="store_true")
    p.set_defaults(fn=cmd_advance)
    p = sub.add_parser("approve")
    p.add_argument("--state", required=True)
    p.add_argument("--approval-id", required=True)
    p.add_argument("--agent", required=True)
    p.add_argument("--action", required=True)
    p.set_defaults(fn=cmd_approve)
    p = sub.add_parser("check")
    p.add_argument("--state", required=True)
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("status")
    p.add_argument("--state", required=True)
    p.set_defaults(fn=cmd_status)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
