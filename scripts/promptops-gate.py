#!/usr/bin/env python3
"""promptops-gate.py — 提示词与配置管理 SOP 门禁（SOP v1.0，2026-11-10 董事长令）。

五步链 version → review → ci → canary → production。
语义版本号、PR 评审、CI 黄金集、灰度 1→10→50→100、回滚=移动 production 标签。
违规一律 exit 1，打印 VIOLATION，绝不静默放行。

用法：
  init --prompt-id <id> --workdir <dir> [--model <模型版本>]
  advance --state <state.json> --stage <阶段> --actor <席位> --artifact <文件>
          [--version <X.Y.Z>] [--tier <1|10|50|100>]
  rollback --state <state.json> --actor <席位> --to-version <X.Y.Z> --reason <原因>
  check --state <state.json>
  status --state <state.json>
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

STAGES = ["version", "review", "ci", "canary", "production"]
TIERS = [1, 10, 50, 100]
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


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
    state_path = os.path.join(args.workdir, "promptops-sop-state.json")
    if os.path.exists(state_path):
        print(f"ERROR: state 已存在 {state_path}，不许覆盖", file=sys.stderr)
        sys.exit(2)
    state = {
        "prompt_id": args.prompt_id,
        "model": args.model or "",
        "created_at": now_iso(),
        "version": None,
        "production_version": None,
        "production_model": None,
        "previous_production_version": None,
        "canary_tier": 0,
        "canary_observed": [],
        "ci_passed": False,
        "stages": {s: {"status": "pending", "actor": None,
                        "artifact": None, "ts": None}
                   for s in STAGES},
    }
    save_state(state_path, state)
    print(f"OK: prompt {args.prompt_id} 已建 {state_path}")


def cmd_advance(args):
    state = load_state(args.state)
    if args.stage not in STAGES:
        print(f"ERROR: 未知阶段 {args.stage}", file=sys.stderr)
        sys.exit(2)
    idx = STAGES.index(args.stage)
    cur = state["stages"][args.stage]
    if cur["status"] == "done":
        fail(f"阶段 {args.stage} 已完成，不许重复推进")
    for prev in STAGES[:idx]:
        if state["stages"][prev]["status"] != "done":
            fail(f"阶段 {args.stage} 之前 {prev} 未完成，不许跳步")
    if not args.artifact:
        fail(f"阶段 {args.stage} 缺产出物 --artifact")
    if not artifact_exists(args.state, args.artifact):
        fail(f"阶段 {args.stage} 产出物不存在：{args.artifact}")
    # version：语义版本号合规
    if args.stage == "version":
        if not args.version or not SEMVER_RE.match(args.version):
            fail(f"版本号不合规：{args.version}，须为 X.Y.Z（MAJOR.MINOR.PATCH）")
        state["version"] = args.version
    # review：评审记录就是产出物本身（已验存在）
    # ci：CI 结果文件须写明全部通过（内容含 PASS 字样且不含 FAIL）
    if args.stage == "ci":
        path = args.artifact if os.path.isabs(args.artifact) else os.path.join(
            base_dir(args.state), args.artifact)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        if "PASS" not in content or "FAIL" in content:
            fail("CI 黄金集未全部通过，不许灰度")
        state["ci_passed"] = True
    # canary：逐档推进，每档须 --tier 且等于下一档；观测记录就是产出物
    if args.stage == "canary":
        if args.tier is None:
            fail("灰度须带 --tier（1/10/50/100）")
        if args.tier not in TIERS:
            fail(f"灰度档位非法：{args.tier}")
        expected = TIERS[len(state["canary_observed"])]
        if args.tier != expected:
            fail(f"灰度须逐档推进，下一档应为 {expected}%，不是 {args.tier}%")
        state["canary_observed"].append(args.tier)
        state["canary_tier"] = args.tier
        if args.tier != 100:
            # 未到 100% 时 canary 阶段保持 pending，等 100% 档才算 done
            save_state(args.state, state)
            print(f"OK: 灰度 {args.tier}% 已观测，下一档 {TIERS[len(state['canary_observed'])]}%")
            return
    # production：标签须同时钉死提示词版本与模型版本
    if args.stage == "production":
        if not state.get("version"):
            fail("production 标签缺提示词版本")
        if not state.get("model"):
            fail("production 标签缺模型版本（init 时须带 --model）")
        state["previous_production_version"] = state.get("production_version")
        state["production_version"] = state["version"]
        state["production_model"] = state["model"]
    cur.update({"status": "done", "actor": args.actor,
                "artifact": args.artifact, "ts": now_iso()})
    save_state(args.state, state)
    print(f"OK: {args.stage} done by {args.actor}")


def cmd_rollback(args):
    state = load_state(args.state)
    if not state.get("production_version"):
        fail("尚未发布 production，不许回滚")
    if not args.to_version or not SEMVER_RE.match(args.to_version):
        fail(f"回滚目标版本不合规：{args.to_version}")
    if state.get("production_version") == args.to_version:
        fail("回滚目标就是当前 production 版，无需回滚")
    state["previous_production_version"] = state.get("production_version")
    state["production_version"] = args.to_version
    state.setdefault("events", []).append(
        {"type": "rollback", "actor": args.actor,
         "to_version": args.to_version, "reason": args.reason, "ts": now_iso()})
    save_state(args.state, state)
    print(f"OK: production 标签已回滚到 {args.to_version}（{args.reason}）")


def check_state(state, state_path):
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
    if state["stages"]["version"]["status"] == "done":
        if not state.get("version") or not SEMVER_RE.match(state["version"]):
            problems.append("version 已完成但版本号不合规")
    if state["stages"]["ci"]["status"] == "done" and not state.get("ci_passed"):
        problems.append("ci 已完成但 CI 未通过")
    if state["stages"]["canary"]["status"] == "done":
        if state.get("canary_observed") != TIERS:
            problems.append(f"canary 已完成但灰度档位不完整：{state.get('canary_observed')}")
    if state["stages"]["production"]["status"] == "done":
        if not state.get("production_version") or not state.get("production_model"):
            problems.append("production 已完成但标签缺提示词版本或模型版本")
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
    print(f"prompt={state['prompt_id']} version={state.get('version')} "
          f"production={state.get('production_version')}@{state.get('production_model')} "
          f"canary={state.get('canary_observed')}")
    for s in STAGES:
        st = state["stages"][s]
        mark = "DONE " if st["status"] == "done" else ".... "
        extra = f" by {st['actor']}" if st["actor"] else ""
        print(f"  {mark}{s}{extra}")


def main():
    ap = argparse.ArgumentParser(description="提示词与配置管理 SOP 门禁")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--prompt-id", required=True)
    p.add_argument("--workdir", required=True)
    p.add_argument("--model", default="")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("advance")
    p.add_argument("--state", required=True)
    p.add_argument("--stage", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--artifact", required=True)
    p.add_argument("--version", default=None)
    p.add_argument("--tier", type=int, default=None)
    p.set_defaults(fn=cmd_advance)
    p = sub.add_parser("rollback")
    p.add_argument("--state", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--to-version", required=True)
    p.add_argument("--reason", required=True)
    p.set_defaults(fn=cmd_rollback)
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
