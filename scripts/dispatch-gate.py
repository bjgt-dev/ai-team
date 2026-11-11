#!/usr/bin/env python3
"""dispatch-gate.py — 协作派发 SOP 门禁（SOP v1.0，2026-11-10 董事长令）。

阶段状态机：contract -> claim -> execute -> receipt，缺前置不许推进。
校验：契约五项齐全、租约原子认领 + 心跳续租 + 过期自动释放重派、
幂等键（attempt ID）去重、迟到回调拒收、重复执行不得重复记账。
状态机放持久存储（state 文件），不放模型对话里。
唯一放行口径：口头“好了”不算数，state 文件说了算。
违规一律 exit 1，打印 VIOLATION，绝不静默放行。

用法：
  init --job-id <id> --workdir <dir> [--lease-ttl-seconds <秒，默认 1800>]
  advance --state <state.json> --stage <阶段> --actor <席位> --artifact <文件>
          [--attempt-id <id>] [--contract-goal <目标>] [--input-version <版本>]
          [--output-schema <Schema>] [--budget <预算>] [--success-criteria <标准>]
          [--lease-ttl-seconds <秒>]
  heartbeat --state <state.json> --attempt-id <id> [--lease-ttl-seconds <秒>]
  check --state <state.json>              # 全量校验
  status --state <state.json>             # 阶段表 + 租约表
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

STAGES = ["contract", "claim", "execute", "receipt"]
DEFAULT_TTL_SECONDS = 1800


def now():
    return datetime.now(timezone.utc)


def now_iso():
    return now().isoformat(timespec="seconds")


def parse_iso(s):
    return datetime.fromisoformat(s)


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


def artifact_exists(state_path, artifact):
    if os.path.isabs(artifact):
        return os.path.exists(artifact)
    return os.path.exists(os.path.join(base_dir(state_path), artifact))


def lease_valid(state, at=None):
    """租约有效：存在、未释放、未过期。"""
    lease = state.get("lease")
    if not lease or not lease.get("attempt_id"):
        return False
    if lease.get("status") != "active":
        return False
    try:
        return parse_iso(lease["expires_at"]) > (at or now())
    except (KeyError, ValueError, TypeError):
        return False


def lease_expired(state):
    lease = state.get("lease")
    if not lease or not lease.get("attempt_id"):
        return False
    if lease.get("status") != "active":
        return False
    try:
        return parse_iso(lease["expires_at"]) <= now()
    except (KeyError, ValueError, TypeError):
        return True


def cmd_init(args):
    os.makedirs(args.workdir, exist_ok=True)
    state_path = os.path.join(args.workdir, "dispatch-sop-state.json")
    if os.path.exists(state_path):
        print(f"ERROR: state 已存在 {state_path}，不许覆盖", file=sys.stderr)
        sys.exit(2)
    state = {
        "job_id": args.job_id,
        "created_at": now_iso(),
        "lease_ttl_seconds": args.lease_ttl_seconds,
        "contract": None,
        "stages": {s: {"status": "pending", "actor": None,
                        "artifact": None, "ts": None, "attempt_id": None}
                   for s in STAGES},
        "lease": None,
        "used_attempt_ids": [],
        "receipt_count": 0,
        "receipt_attempt_id": None,
    }
    save_state(state_path, state)
    print(f"OK: job {args.job_id} 已建 {state_path} lease_ttl={args.lease_ttl_seconds}s")


def validate_advance(state, state_path, stage, actor, artifact, attempt_id,
                     contract_fields, ttl):
    idx = STAGES.index(stage)
    cur = state["stages"][stage]
    # 0. 重派 / 重做规则：只有 claim 允许在租约过期后重认领；其余已完成阶段不许重复推进
    if cur["status"] == "done" and stage != "claim":
        fail(f"阶段 {stage} 已完成，不许重复推进；重派须走 claim 换新 attempt ID")
    if stage == "claim" and cur["status"] == "done":
        # 重派只允许在当前租约过期（或已释放）时，且必须换新 attempt ID
        if lease_valid(state):
            fail(f"当前租约仍有效（attempt {state['lease']['attempt_id']}），不许重派；"
                 "等租约过期或心跳停后再重派")
        if not attempt_id:
            fail("重派须带新 --attempt-id")
        if attempt_id in state.get("used_attempt_ids", []):
            fail(f"attempt ID 重复：{attempt_id} 已用过，重派须换新 attempt ID")
    # 1. 顺序：前面阶段必须全 done（claim 重派时 execute/receipt 已被重置，见 cmd_advance）
    for prev in STAGES[:idx]:
        if state["stages"][prev]["status"] != "done":
            fail(f"阶段 {stage} 之前 {prev} 未完成，不许跳步")
    # 2. 产出物必须存在
    if not artifact:
        fail(f"阶段 {stage} 缺产出物 --artifact")
    if not artifact_exists(state_path, artifact):
        fail(f"阶段 {stage} 产出物不存在：{artifact}")
    # 3. 契约五项齐全，缺一项不许派发
    if stage == "contract":
        missing = [k for k, v in contract_fields.items() if not v or not str(v).strip()]
        if missing:
            fail(f"契约缺项不许派发，缺：{','.join(missing)}"
                 "（目标/输入版本/输出 Schema/预算/成功标准五项齐全才可派发）")
    # 4. 认领：须带 attempt ID、须未用过、租约原子化（已有有效租约不许再认领）
    if stage == "claim":
        if not attempt_id:
            fail("认领须带 --attempt-id（幂等键），缺 attempt ID 不许认领")
        if cur["status"] != "done" and attempt_id in state.get("used_attempt_ids", []):
            fail(f"attempt ID 重复：{attempt_id} 已用过，认领须换新 attempt ID")
        if lease_valid(state):
            fail(f"已有有效租约（attempt {state['lease']['attempt_id']}），不许重复认领")
    # 5. 执行 / 回执：attempt ID 必须与当前租约一致，租约过期不许执行（迟到执行拒收）
    if stage in ("execute", "receipt"):
        if not attempt_id:
            fail(f"阶段 {stage} 须带 --attempt-id，须与当前租约一致")
        lease = state.get("lease")
        if not lease or not lease.get("attempt_id"):
            fail(f"阶段 {stage} 无租约，不许推进")
        if attempt_id != lease["attempt_id"]:
            fail(f"阶段 {stage} 的 attempt ID {attempt_id} 与当前租约 "
                 f"{lease['attempt_id']} 不一致，迟到回调/过期 attempt 拒收")
        if lease_expired(state):
            fail(f"阶段 {stage} 租约已过期（attempt {attempt_id}，"
                 f"expires_at={lease.get('expires_at')}），不许执行；须重派新 attempt ID")
        if lease.get("status") != "active":
            fail(f"阶段 {stage} 租约已释放/关闭（attempt {attempt_id}），不许推进")
    # 6. 回执：只能记账一次，重复回执不得重复记账
    if stage == "receipt":
        if state.get("receipt_count", 0) >= 1:
            fail("回执已记账一次，重复回执拒收，不得重复记账")


def cmd_advance(args):
    state = load_state(args.state)
    if args.stage not in STAGES:
        print(f"ERROR: 未知阶段 {args.stage}", file=sys.stderr)
        sys.exit(2)
    ttl = args.lease_ttl_seconds if args.lease_ttl_seconds is not None \
        else state.get("lease_ttl_seconds", DEFAULT_TTL_SECONDS)
    contract_fields = {
        "goal": args.contract_goal,
        "input_version": args.input_version,
        "output_schema": args.output_schema,
        "budget": args.budget,
        "success_criteria": args.success_criteria,
    }
    validate_advance(state, args.state, args.stage, args.actor, args.artifact,
                     args.attempt_id, contract_fields, ttl)
    cur = state["stages"][args.stage]
    if args.stage == "contract":
        state["contract"] = {k: v for k, v in contract_fields.items()}
    if args.stage == "claim":
        # 重派：下游 execute/receipt 重置为 pending，旧租约作废
        if cur["status"] == "done":
            for later in ("execute", "receipt"):
                state["stages"][later] = {"status": "pending", "actor": None,
                                          "artifact": None, "ts": None,
                                          "attempt_id": None}
            print(f"NOTE: 重派新 attempt {args.attempt_id}，下游 execute/receipt 已重置")
        claimed = now()
        state["lease"] = {
            "attempt_id": args.attempt_id,
            "holder": args.actor,
            "claimed_at": claimed.isoformat(timespec="seconds"),
            "last_heartbeat_at": claimed.isoformat(timespec="seconds"),
            "expires_at": (claimed + timedelta(seconds=ttl)).isoformat(timespec="seconds"),
            "ttl_seconds": ttl,
            "status": "active",
        }
        state["used_attempt_ids"].append(args.attempt_id)
    if args.stage == "receipt":
        state["receipt_count"] = state.get("receipt_count", 0) + 1
        state["receipt_attempt_id"] = args.attempt_id
        if state.get("lease"):
            state["lease"]["status"] = "released"
    cur.update({"status": "done", "actor": args.actor,
                "artifact": args.artifact, "ts": now_iso(),
                "attempt_id": args.attempt_id})
    save_state(args.state, state)
    print(f"OK: {args.stage} done by {args.actor}"
          + (f" attempt={args.attempt_id}" if args.attempt_id else ""))


def cmd_heartbeat(args):
    state = load_state(args.state)
    lease = state.get("lease")
    if not lease or not lease.get("attempt_id"):
        fail("无租约，心跳无处续租")
    if args.attempt_id != lease["attempt_id"]:
        fail(f"心跳 attempt ID {args.attempt_id} 与当前租约 "
             f"{lease['attempt_id']} 不一致，迟到心跳拒收")
    if lease.get("status") != "active":
        fail(f"租约已 {lease.get('status')}，不许心跳续租")
    if lease_expired(state):
        fail(f"租约已过期（expires_at={lease.get('expires_at')}），心跳拒收；须重派新 attempt ID")
    ttl = args.lease_ttl_seconds if args.lease_ttl_seconds is not None \
        else lease.get("ttl_seconds", state.get("lease_ttl_seconds", DEFAULT_TTL_SECONDS))
    t = now()
    lease["last_heartbeat_at"] = t.isoformat(timespec="seconds")
    lease["expires_at"] = (t + timedelta(seconds=ttl)).isoformat(timespec="seconds")
    lease["ttl_seconds"] = ttl
    save_state(args.state, state)
    print(f"OK: 心跳续租 attempt={args.attempt_id} expires_at={lease['expires_at']}")


def check_state(state, state_path):
    """全量校验：返回违规列表（空 = 通过）。"""
    problems = []
    # 顺序完整性：done 的阶段之前必须全 done
    for i, s in enumerate(STAGES):
        if state["stages"][s]["status"] == "done":
            for prev in STAGES[:i]:
                if state["stages"][prev]["status"] != "done":
                    problems.append(f"{s} 已完成但前置 {prev} 未完成")
    # 产出物存在
    for s in STAGES:
        st = state["stages"][s]
        if st["status"] == "done":
            if not st.get("artifact") or not artifact_exists(state_path, st["artifact"]):
                problems.append(f"{s} 产出物缺失：{st.get('artifact')}")
    # 契约五项齐全
    if state["stages"]["contract"]["status"] == "done":
        c = state.get("contract") or {}
        for k in ("goal", "input_version", "output_schema", "budget", "success_criteria"):
            if not c.get(k) or not str(c[k]).strip():
                problems.append(f"契约缺项：{k}")
    # 租约一致性：claim done 必须有租约，attempt 必须在已用集合里
    if state["stages"]["claim"]["status"] == "done":
        lease = state.get("lease")
        if not lease or not lease.get("attempt_id"):
            problems.append("claim 已完成但 state 里无租约（手工篡改）")
        else:
            if lease["attempt_id"] not in state.get("used_attempt_ids", []):
                problems.append(f"租约 attempt {lease['attempt_id']} 不在已用 attempt 集合里")
            if state["stages"]["claim"].get("attempt_id") != lease["attempt_id"]:
                problems.append("claim 阶段 attempt 与租约 attempt 不一致")
            try:
                if parse_iso(lease["expires_at"]) <= parse_iso(lease["claimed_at"]):
                    problems.append("租约 expires_at 不晚于 claimed_at")
            except (KeyError, ValueError, TypeError):
                problems.append("租约时间字段缺失或非法")
    # 执行 / 回执 attempt 必须与租约一致，且不得是已作废的旧 attempt
    for s in ("execute", "receipt"):
        st = state["stages"][s]
        if st["status"] == "done":
            lease = state.get("lease")
            if not lease or st.get("attempt_id") != lease.get("attempt_id"):
                problems.append(f"{s} 的 attempt {st.get('attempt_id')} 与租约 "
                                f"{(lease or {}).get('attempt_id')} 不一致（迟到回调）")
            if st.get("attempt_id") not in state.get("used_attempt_ids", []):
                problems.append(f"{s} 的 attempt {st.get('attempt_id')} 未登记为已用")
    # 回执只能记账一次
    if state.get("receipt_count", 0) > 1:
        problems.append(f"回执记账 {state['receipt_count']} 次，重复记账")
    if state["stages"]["receipt"]["status"] == "done":
        if state.get("receipt_count", 0) != 1:
            problems.append("receipt 已完成但 receipt_count 不为 1")
        if state.get("receipt_attempt_id") != state["stages"]["receipt"].get("attempt_id"):
            problems.append("回执 attempt 与 receipt_attempt_id 不一致")
    # attempt ID 不得重复登记
    used = state.get("used_attempt_ids", [])
    if len(used) != len(set(used)):
        problems.append("used_attempt_ids 有重复登记")
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
    print(f"job={state['job_id']} receipt_count={state.get('receipt_count', 0)} "
          f"used_attempts={state.get('used_attempt_ids', [])}")
    for s in STAGES:
        st = state["stages"][s]
        mark = "DONE " if st["status"] == "done" else ".... "
        extra = f" by {st['actor']}" if st["actor"] else ""
        att = f" attempt={st['attempt_id']}" if st.get("attempt_id") else ""
        print(f"  {mark}{s}{extra}{att}")
    lease = state.get("lease")
    if lease:
        print(f"  lease attempt={lease['attempt_id']} holder={lease.get('holder')} "
              f"status={lease.get('status')} expires_at={lease.get('expires_at')}")
    else:
        print("  lease none")


def main():
    ap = argparse.ArgumentParser(description="协作派发 SOP 门禁")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--job-id", required=True)
    p.add_argument("--workdir", required=True)
    p.add_argument("--lease-ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("advance")
    p.add_argument("--state", required=True)
    p.add_argument("--stage", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--artifact", required=True)
    p.add_argument("--attempt-id", default=None)
    p.add_argument("--contract-goal", default=None)
    p.add_argument("--input-version", default=None)
    p.add_argument("--output-schema", default=None)
    p.add_argument("--budget", default=None)
    p.add_argument("--success-criteria", default=None)
    p.add_argument("--lease-ttl-seconds", type=int, default=None)
    p.set_defaults(fn=cmd_advance)
    p = sub.add_parser("heartbeat")
    p.add_argument("--state", required=True)
    p.add_argument("--attempt-id", required=True)
    p.add_argument("--lease-ttl-seconds", type=int, default=None)
    p.set_defaults(fn=cmd_heartbeat)
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
