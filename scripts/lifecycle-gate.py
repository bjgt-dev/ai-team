#!/usr/bin/env python3
"""lifecycle-gate.py — 生命周期 SOP 门禁（SOP v1.0，2026-11-10 董事长令）。

状态机 register → review → deploy → monitor → improve（重走评审）/ retire。
发布门三项证据；degrade/rollback/revoke/kill；退役须依赖清单清空。
违规一律 exit 1，打印 VIOLATION，绝不静默放行。

用法：
  init --agent-id <id> --workdir <dir> [--owner <席位>] [--purpose <用途>]
  advance --state <state.json> --stage <阶段> --actor <席位> --artifact <文件>
  degrade --state <state.json> --actor <席位> --reason <原因>
  rollback --state <state.json> --actor <席位> --to <回滚点>
  revoke --state <state.json> --actor <席位> --reason <原因>
  kill --state <state.json> --actor <席位> --reason <原因>
  check --state <state.json>
  status --state <state.json>
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

STAGES = ["register", "review", "deploy", "monitor", "improve", "retire"]
RELEASE_GATE_FILES = ["accuracy.md", "safety.md", "readiness.md"]


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


def artifact_exists(state_path, artifact):
    if os.path.isabs(artifact):
        return os.path.exists(artifact)
    return os.path.exists(os.path.join(base_dir(state_path), artifact))


def cmd_init(args):
    os.makedirs(args.workdir, exist_ok=True)
    state_path = os.path.join(args.workdir, "lifecycle-sop-state.json")
    if os.path.exists(state_path):
        print(f"ERROR: state 已存在 {state_path}，不许覆盖", file=sys.stderr)
        sys.exit(2)
    state = {
        "agent_id": args.agent_id,
        "owner": args.owner or "",
        "purpose": args.purpose or "",
        "created_at": now_iso(),
        "status": "active",
        "killed": False,
        "retired": False,
        "degraded": False,
        "rolled_back_to": None,
        "revoked": False,
        "dependencies": [d for d in args.deps.split(",") if d] if args.deps else [],
        "release_gate": {f: False for f in RELEASE_GATE_FILES},
        "stages": {s: {"status": "pending", "actor": None,
                        "artifact": None, "ts": None}
                   for s in STAGES},
    }
    save_state(state_path, state)
    print(f"OK: agent {args.agent_id} 已登记 {state_path}")


def guard_alive(state):
    if state.get("killed"):
        fail("agent 已 kill，不许再推进；复活须重新登记 + 重走评审")
    if state.get("retired"):
        fail("agent 已退役，不许再推进")


def cmd_advance(args):
    state = load_state(args.state)
    if args.stage not in STAGES:
        print(f"ERROR: 未知阶段 {args.stage}", file=sys.stderr)
        sys.exit(2)
    guard_alive(state)
    idx = STAGES.index(args.stage)
    cur = state["stages"][args.stage]
    if cur["status"] == "done":
        if args.stage != "improve":
            fail(f"阶段 {args.stage} 已完成，不许重复推进")
        # improve 允许重复：每次改进重走评审
    # improve 只能在 monitor 完成后做
    if args.stage == "improve" and state["stages"]["monitor"]["status"] != "done":
        fail("improve 须在 monitor 完成后做")
    # retire 只能在 monitor 或 improve 完成后做
    if args.stage == "retire" and state["stages"]["monitor"]["status"] != "done":
        fail("retire 须在 monitor 完成后做")
    # 顺序：前面阶段必须全 done（improve/retire 例外，见上）
    if args.stage not in ("improve", "retire"):
        for prev in STAGES[:idx]:
            if state["stages"][prev]["status"] != "done":
                fail(f"阶段 {args.stage} 之前 {prev} 未完成，不许跳步")
    # 产出物必须存在
    if not args.artifact:
        fail(f"阶段 {args.stage} 缺产出物 --artifact")
    if not artifact_exists(args.state, args.artifact):
        fail(f"阶段 {args.stage} 产出物不存在：{args.artifact}")
    # 发布门：deploy 须三项证据文件都在 state 目录
    if args.stage == "deploy":
        for f in RELEASE_GATE_FILES:
            if not artifact_exists(args.state, f):
                fail(f"发布门缺证据文件：{f}（准确/安全/就绪三项缺一不许上线）")
            state["release_gate"][f] = True
    # 退役：依赖清单须清空
    if args.stage == "retire":
        if state.get("dependencies"):
            fail(f"退役前依赖清单未清空：{state['dependencies']}，须先迁移")
        state["retired"] = True
        state["status"] = "retired"
    cur.update({"status": "done", "actor": args.actor,
                "artifact": args.artifact, "ts": now_iso()})
    # improve 完成后，review/deploy/monitor 重置重走
    if args.stage == "improve":
        for s in ["review", "deploy", "monitor"]:
            state["stages"][s] = {"status": "pending", "actor": None,
                                  "artifact": None, "ts": None}
        state["release_gate"] = {f: False for f in RELEASE_GATE_FILES}
        print(f"NOTE: improve 完成，review/deploy/monitor 已重置重走")
    save_state(args.state, state)
    print(f"OK: {args.stage} done by {args.actor}")


def cmd_depend(args):
    state = load_state(args.state)
    guard_alive(state)
    if args.dep not in state["dependencies"]:
        state["dependencies"].append(args.dep)
    save_state(args.state, state)
    print(f"OK: 依赖已登记 {args.dep}")


def cmd_undepend(args):
    state = load_state(args.state)
    guard_alive(state)
    if args.dep not in state["dependencies"]:
        print(f"ERROR: 依赖不存在：{args.dep}", file=sys.stderr)
        sys.exit(2)
    state["dependencies"].remove(args.dep)
    save_state(args.state, state)
    print(f"OK: 依赖已迁移/移除 {args.dep}")


def cmd_degrade(args):
    state = load_state(args.state)
    guard_alive(state)
    if state["stages"]["deploy"]["status"] != "done":
        fail("未上线不许降级")
    state["degraded"] = True
    state["status"] = "degraded"
    state.setdefault("events", []).append(
        {"type": "degrade", "actor": args.actor, "reason": args.reason, "ts": now_iso()})
    save_state(args.state, state)
    print(f"OK: 已降级（{args.reason}）")


def cmd_rollback(args):
    state = load_state(args.state)
    guard_alive(state)
    if state["stages"]["deploy"]["status"] != "done":
        fail("未上线不许回滚")
    state["rolled_back_to"] = args.to
    state["status"] = "rolled_back"
    state.setdefault("events", []).append(
        {"type": "rollback", "actor": args.actor, "to": args.to, "ts": now_iso()})
    save_state(args.state, state)
    print(f"OK: 已回滚到 {args.to}")


def cmd_revoke(args):
    state = load_state(args.state)
    guard_alive(state)
    state["revoked"] = True
    state.setdefault("events", []).append(
        {"type": "revoke", "actor": args.actor, "reason": args.reason, "ts": now_iso()})
    save_state(args.state, state)
    print(f"OK: 已撤权（{args.reason}）")


def cmd_kill(args):
    state = load_state(args.state)
    if state.get("killed"):
        fail("agent 已 kill，不许重复 kill")
    state["killed"] = True
    state["status"] = "killed"
    state.setdefault("events", []).append(
        {"type": "kill", "actor": args.actor, "reason": args.reason, "ts": now_iso()})
    save_state(args.state, state)
    print(f"OK: kill switch 已触发（{args.reason}），agent 已停用")


def check_state(state, state_path):
    problems = []
    for i, s in enumerate(STAGES):
        if s == "improve":
            continue  # improve 完成会重置 review/deploy/monitor，前置 pending 是合法的
        if state["stages"][s]["status"] == "done":
            for prev in STAGES[:i]:
                if prev in ("improve", "retire"):
                    continue
                if state["stages"][prev]["status"] != "done":
                    problems.append(f"{s} 已完成但前置 {prev} 未完成")
    for s in STAGES:
        st = state["stages"][s]
        if st["status"] == "done":
            if not st.get("artifact") or not artifact_exists(state_path, st["artifact"]):
                problems.append(f"{s} 产出物缺失：{st.get('artifact')}")
    if state["stages"]["deploy"]["status"] == "done":
        for f in RELEASE_GATE_FILES:
            if not state["release_gate"].get(f):
                problems.append(f"deploy 已完成但发布门缺证据：{f}")
            if not artifact_exists(state_path, f):
                problems.append(f"deploy 已完成但发布门证据文件缺失：{f}")
    if state.get("retired") and state.get("dependencies"):
        problems.append(f"已退役但依赖清单未清空：{state['dependencies']}")
    if state.get("killed"):
        for s in STAGES:
            if state["stages"][s]["status"] == "done" and s == "retire":
                problems.append("已 kill 又退役，状态矛盾")
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
    print(f"agent={state['agent_id']} owner={state.get('owner','')} status={state['status']} "
          f"killed={state.get('killed')} retired={state.get('retired')} degraded={state.get('degraded')}")
    for s in STAGES:
        st = state["stages"][s]
        mark = "DONE " if st["status"] == "done" else ".... "
        extra = f" by {st['actor']}" if st["actor"] else ""
        print(f"  {mark}{s}{extra}")


def main():
    ap = argparse.ArgumentParser(description="生命周期 SOP 门禁")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--agent-id", required=True)
    p.add_argument("--workdir", required=True)
    p.add_argument("--owner", default="")
    p.add_argument("--purpose", default="")
    p.add_argument("--deps", default="")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("advance")
    p.add_argument("--state", required=True)
    p.add_argument("--stage", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--artifact", required=True)
    p.set_defaults(fn=cmd_advance)
    for name, fn in [("degrade", cmd_degrade), ("rollback", cmd_rollback),
                     ("revoke", cmd_revoke), ("kill", cmd_kill)]:
        p = sub.add_parser(name)
        p.add_argument("--state", required=True)
        p.add_argument("--actor", required=True)
        if name == "rollback":
            p.add_argument("--to", required=True)
        else:
            p.add_argument("--reason", required=True)
        p.set_defaults(fn=fn)
    for name, fn in [("depend", cmd_depend), ("undepend", cmd_undepend)]:
        p = sub.add_parser(name)
        p.add_argument("--state", required=True)
        p.add_argument("--dep", required=True)
        p.set_defaults(fn=fn)
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
