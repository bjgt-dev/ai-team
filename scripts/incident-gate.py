#!/usr/bin/env python3
"""incident-gate.py — 事故与交接 SOP 门禁（SOP v1.0，2026-11-10 董事长令）。

七步链 declare → triage → mitigate → diagnose → resolve → postmortem → runbook。
SEV1–SEV4；IC/scribe/comms 三角色；先缓解后诊断；
无责复盘 5 个工作日内（门禁按 7 自然日计）；复盘后 runbook 必须更新；
交接清单四项齐全。
违规一律 exit 1，打印 VIOLATION，绝不静默放行。

用法：
  init --incident-id <id> --workdir <dir> --sev <1|2|3|4> [--age-days <N>]
  advance --state <state.json> --stage <阶段> --actor <席位> --artifact <文件>
  handover --state <state.json> --actor <席位> --artifact <交接文件> --to <接收方>
  check --state <state.json>
  status --state <state.json>
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

STAGES = ["declare", "triage", "mitigate", "diagnose", "resolve",
          "postmortem", "runbook"]
SEVS = [1, 2, 3, 4]
POSTMORTEM_LIMIT_SECONDS = 7 * 86400  # 5 个工作日按 7 自然日计
HANDOVER_SECTIONS = ["告警", "变更", "脆弱", "跟进"]


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


def read_content(state_path, name):
    p = name if os.path.isabs(name) else os.path.join(base_dir(state_path), name)
    with open(p, encoding="utf-8") as f:
        return f.read()


def cmd_init(args):
    os.makedirs(args.workdir, exist_ok=True)
    state_path = os.path.join(args.workdir, "incident-sop-state.json")
    if os.path.exists(state_path):
        print(f"ERROR: state 已存在 {state_path}，不许覆盖", file=sys.stderr)
        sys.exit(2)
    if args.sev not in SEVS:
        print(f"ERROR: 级别非法 {args.sev}，须为 1/2/3/4", file=sys.stderr)
        sys.exit(2)
    state = {
        "incident_id": args.incident_id,
        "sev": args.sev,
        "created_at": now_iso(),
        "created_ts": time.time() - (args.age_days or 0) * 86400,
        "roles": {},
        "handover": None,
        "stages": {s: {"status": "pending", "actor": None,
                        "artifact": None, "ts": None}
                   for s in STAGES},
    }
    save_state(state_path, state)
    print(f"OK: 事故 {args.incident_id} 已宣告（SEV{args.sev}）{state_path}")


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
    if not os.path.exists(args.artifact if os.path.isabs(args.artifact)
                          else os.path.join(base_dir(args.state), args.artifact)):
        fail(f"阶段 {args.stage} 产出物不存在：{args.artifact}")
    content = read_content(args.state, args.artifact)
    # triage：三角色须在文件里齐全（IC/scribe/comms 各一行）
    if args.stage == "triage":
        for role in ["IC", "scribe", "comms"]:
            if role not in content:
                fail(f"triage 缺角色：{role}（IC/scribe/comms 三角色缺一不许缓解）")
        state["roles"] = {"IC": True, "scribe": True, "comms": True}
    # postmortem：改进项须带负责人与到期日；P0/P1 须 5 个工作日内
    if args.stage == "postmortem":
        if "负责人" not in content or "期限" not in content:
            fail("复盘改进项须带负责人 + 到期日，缺一项不许过")
        if not re.search(r"20\d\d-\d\d-\d\d", content):
            fail("复盘改进项到期日须写明日期（YYYY-MM-DD）")
        if state["sev"] in (1, 2):
            if time.time() - state["created_ts"] > POSTMORTEM_LIMIT_SECONDS:
                fail("P0/P1 复盘超 5 个工作日（按 7 自然日计），已逾期")
    cur.update({"status": "done", "actor": args.actor,
                "artifact": args.artifact, "ts": now_iso()})
    save_state(args.state, state)
    print(f"OK: {args.stage} done by {args.actor}")


def cmd_handover(args):
    state = load_state(args.state)
    if not os.path.exists(args.artifact if os.path.isabs(args.artifact)
                          else os.path.join(base_dir(args.state), args.artifact)):
        fail(f"交接文件不存在：{args.artifact}")
    content = read_content(args.state, args.artifact)
    for section in HANDOVER_SECTIONS:
        if section not in content:
            fail(f"交接清单缺项：{section}（未关告警/进行中变更/已知脆弱点/待跟进项缺一不许交接）")
    if not args.to:
        fail("交接须写明接收方 --to")
    state["handover"] = {"actor": args.actor, "to": args.to,
                         "artifact": args.artifact, "ts": now_iso()}
    save_state(args.state, state)
    print(f"OK: 交接完成（{args.actor} → {args.to}）")


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
            if not st.get("artifact"):
                problems.append(f"{s} 产出物缺失")
                continue
            try:
                content = read_content(state_path, st["artifact"])
            except OSError:
                problems.append(f"{s} 产出物文件缺失：{st['artifact']}")
                continue
            if s == "triage":
                for role in ["IC", "scribe", "comms"]:
                    if role not in content:
                        problems.append(f"triage 缺角色：{role}")
            if s == "postmortem":
                if "负责人" not in content or "期限" not in content:
                    problems.append("复盘改进项缺负责人或到期日")
    if state["stages"]["runbook"]["status"] == "done" and \
            state["stages"]["postmortem"]["status"] != "done":
        problems.append("runbook 已更新但复盘未完成，顺序错乱")
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
    print(f"incident={state['incident_id']} SEV{state['sev']} "
          f"handover={'done' if state.get('handover') else 'pending'}")
    for s in STAGES:
        st = state["stages"][s]
        mark = "DONE " if st["status"] == "done" else ".... "
        extra = f" by {st['actor']}" if st["actor"] else ""
        print(f"  {mark}{s}{extra}")


def main():
    ap = argparse.ArgumentParser(description="事故与交接 SOP 门禁")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--incident-id", required=True)
    p.add_argument("--workdir", required=True)
    p.add_argument("--sev", type=int, required=True)
    p.add_argument("--age-days", type=int, default=0)
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("advance")
    p.add_argument("--state", required=True)
    p.add_argument("--stage", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--artifact", required=True)
    p.set_defaults(fn=cmd_advance)
    p = sub.add_parser("handover")
    p.add_argument("--state", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--artifact", required=True)
    p.add_argument("--to", required=True)
    p.set_defaults(fn=cmd_handover)
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
