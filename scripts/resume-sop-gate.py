#!/usr/bin/env python3
"""resume-sop-gate.py — 简历 SOP 门禁（SOP v1.0，2026-11-10 董事长令）。

阶段状态机 + 分工 / 框架冻结 / 批量意见校验。
唯一放行口径：口头“好了”不算数，state 文件说了算。
违规一律 exit 1，打印 VIOLATION，绝不静默放行。

用法：
  init --job-id <id> --workdir <dir> [--target-role <岗位>]
  advance --state <state.json> --stage <阶段> --actor <席位> --artifact <文件> [--frozen]
  collect-feedback --state <state.json>   # 进入批量收意见模式（改动冻结）
  approve-edit --state <state.json>       # 董事长说“改”后放行一次改完
  check --state <state.json>              # 全量校验
  status --state <state.json>             # 阶段表
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

STAGES = [
    "intake", "jd_analysis", "material", "framework", "drafting",
    "tailor", "ats", "review_content", "review_facts",
    "review_language", "review_layout", "acceptance",
]
REVIEW_STAGES = ["review_content", "review_facts", "review_language", "review_layout"]
CHAIRMAN_ACTORS = {"chairman", "董事长"}


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
    state_path = os.path.join(args.workdir, "resume-sop-state.json")
    if os.path.exists(state_path):
        print(f"ERROR: state 已存在 {state_path}，不许覆盖", file=sys.stderr)
        sys.exit(2)
    state = {
        "job_id": args.job_id,
        "target_role": args.target_role or "",
        "created_at": now_iso(),
        "feedback_mode": "approved",
        "drafter": None,
        "stages": {s: {"status": "pending", "actor": None,
                        "artifact": None, "ts": None, "frozen": False}
                   for s in STAGES},
    }
    save_state(state_path, state)
    print(f"OK: job {args.job_id} 已建 {state_path}")


def validate_advance(state, state_path, stage, actor, artifact, frozen):
    idx = STAGES.index(stage)
    cur = state["stages"][stage]
    # 1. 顺序：前面阶段必须全 done（重做 drafting/tailor 时，下游已重置，见后）
    for prev in STAGES[:idx]:
        if state["stages"][prev]["status"] != "done":
            fail(f"阶段 {stage} 之前 {prev} 未完成，不许跳步")
    # 2. 产出物必须存在
    if not artifact:
        fail(f"阶段 {stage} 缺产出物 --artifact")
    if not artifact_exists(state_path, artifact):
        fail(f"阶段 {stage} 产出物不存在：{artifact}")
    # 3. 框架必须冻结才能写文
    if stage == "framework" and not frozen:
        fail("框架定稿必须带 --frozen，不冻结不许写文")
    if stage in ("drafting", "tailor"):
        fw = state["stages"]["framework"]
        if fw["status"] != "done" or not fw.get("frozen"):
            fail(f"框架未定稿/未冻结，不许进 {stage}")
    # 4. 批量意见模式：收意见期间不许改文
    if stage in ("drafting", "tailor") and state["feedback_mode"] == "collecting":
        fail(f"正在批量收意见（feedback_mode=collecting），{stage} 禁止推进；等董事长说“改”")
    # 5. 分工：撰写席不得兼任审核
    if stage in REVIEW_STAGES:
        if state.get("drafter") and actor == state["drafter"]:
            fail(f"审核轮 {stage} 的审核人 {actor} 就是撰写席，不许自己审自己")
        for other in REVIEW_STAGES:
            o = state["stages"][other]
            if o["status"] == "done" and o["actor"] == actor:
                fail(f"审核人 {actor} 已审过 {other}，四轮必须四人")
    # 6. 验收只能董事长签
    if stage == "acceptance" and actor not in CHAIRMAN_ACTORS:
        fail(f"验收只能董事长签，{actor} 无权验收")


def cmd_advance(args):
    state = load_state(args.state)
    if args.stage not in STAGES:
        print(f"ERROR: 未知阶段 {args.stage}", file=sys.stderr)
        sys.exit(2)
    cur = state["stages"][args.stage]
    if cur["status"] == "done":
        # 重做只允许 drafting/tailor，且须在放行模式；重做使下游全部失效
        if args.stage not in ("drafting", "tailor"):
            fail(f"阶段 {args.stage} 已完成，不许重复推进")
        if state["feedback_mode"] != "approved":
            fail(f"重做 {args.stage} 须先经 approve-edit 放行")
        idx = STAGES.index(args.stage)
        for later in STAGES[idx + 1:]:
            state["stages"][later] = {"status": "pending", "actor": None,
                                      "artifact": None, "ts": None, "frozen": False}
        print(f"NOTE: 重做 {args.stage}，下游阶段已重置为 pending")
    validate_advance(state, args.state, args.stage, args.actor, args.artifact, args.frozen)
    cur = state["stages"][args.stage]
    cur.update({"status": "done", "actor": args.actor,
                "artifact": args.artifact, "ts": now_iso(),
                "frozen": bool(args.frozen) if args.stage == "framework" else False})
    if args.stage == "drafting":
        state["drafter"] = args.actor
    save_state(args.state, state)
    print(f"OK: {args.stage} done by {args.actor}")


def cmd_collect_feedback(args):
    state = load_state(args.state)
    state["feedback_mode"] = "collecting"
    save_state(args.state, state)
    print("OK: feedback_mode=collecting，drafting/tailor 已冻结，只记录不改文")


def cmd_approve_edit(args):
    state = load_state(args.state)
    state["feedback_mode"] = "approved"
    save_state(args.state, state)
    print("OK: feedback_mode=approved，董事长已说“改”，一次改完")


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
    # 框架冻结
    fw = state["stages"]["framework"]
    if state["stages"]["drafting"]["status"] == "done":
        if fw["status"] != "done" or not fw.get("frozen"):
            problems.append("drafting 已完成但框架未定稿/未冻结")
    # 分工
    drafter = state.get("drafter")
    reviewers = []
    for s in REVIEW_STAGES:
        st = state["stages"][s]
        if st["status"] == "done":
            if drafter and st["actor"] == drafter:
                problems.append(f"{s} 审核人就是撰写席")
            reviewers.append(st["actor"])
    if len(reviewers) != len(set(reviewers)):
        problems.append("四轮审核人有重复，必须四人")
    # 验收人
    acc = state["stages"]["acceptance"]
    if acc["status"] == "done" and acc["actor"] not in CHAIRMAN_ACTORS:
        problems.append(f"验收人 {acc['actor']} 不是董事长")
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
    print(f"job={state['job_id']} role={state.get('target_role','')} "
          f"feedback_mode={state['feedback_mode']} drafter={state.get('drafter')}")
    for s in STAGES:
        st = state["stages"][s]
        mark = "DONE " if st["status"] == "done" else ".... "
        extra = f" by {st['actor']}" if st["actor"] else ""
        frozen = " [FROZEN]" if st.get("frozen") else ""
        print(f"  {mark}{s}{extra}{frozen}")


def main():
    ap = argparse.ArgumentParser(description="简历 SOP 门禁")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--job-id", required=True)
    p.add_argument("--workdir", required=True)
    p.add_argument("--target-role", default="")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("advance")
    p.add_argument("--state", required=True)
    p.add_argument("--stage", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--artifact", required=True)
    p.add_argument("--frozen", action="store_true")
    p.set_defaults(fn=cmd_advance)
    p = sub.add_parser("collect-feedback")
    p.add_argument("--state", required=True)
    p.set_defaults(fn=cmd_collect_feedback)
    p = sub.add_parser("approve-edit")
    p.add_argument("--state", required=True)
    p.set_defaults(fn=cmd_approve_edit)
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
