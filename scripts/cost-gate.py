#!/usr/bin/env python3
"""cost-gate.py — 成本与额度 SOP 门禁（SOP v1.0，2026-11-10 董事长令）。

预算先行；阈值 70% 告警 / 85% 限流降级 / 100% 硬拦截；
token bucket 限速；熔断器；备用切换（高风险任务禁自动降级，转人工）。
本机制只做本地记账模拟，不产生任何真实费用、不调用任何真实计费接口。
违规一律 exit 1，打印 VIOLATION，绝不静默放行。

用法：
  init --agent-id <id> --workdir <dir> --budget-day <N> --budget-month <N>
       [--bucket-capacity <N>] [--bucket-rate <N>] [--breaker-failures <N>]
       [--cooldown <秒>] [--fallback <备用通道>]
  advance --state <state.json> --stage budget --actor <席位> --artifact <预算文件>
  charge --state <state.json> --actor <席位> --amount <N> --model <模型>
         [--high-risk] [--failed]
  fallback --state <state.json> --actor <席位> --to <备用通道>
  usage --state <state.json>
  check --state <state.json>
  status --state <state.json>
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

STAGES = ["budget"]


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
    state_path = os.path.join(args.workdir, "cost-sop-state.json")
    if os.path.exists(state_path):
        print(f"ERROR: state 已存在 {state_path}，不许覆盖", file=sys.stderr)
        sys.exit(2)
    now = time.time()
    state = {
        "agent_id": args.agent_id,
        "created_at": now_iso(),
        "budget_day": args.budget_day,
        "budget_month": args.budget_month,
        "bucket_capacity": args.bucket_capacity,
        "bucket_rate": args.bucket_rate,
        "bucket_tokens": float(args.bucket_capacity),
        "bucket_last_refill": now,
        "breaker_failures": args.breaker_failures,
        "cooldown": args.cooldown,
        "fallback_channel": args.fallback or "",
        "channel": "primary",
        "used_day": 0,
        "used_month": 0,
        "consecutive_failures": 0,
        "breaker_tripped_until": 0,
        "alerts": [],
        "degrades": [],
        "manual_reviews": [],
        "charges": [],
        "stages": {"budget": {"status": "pending", "actor": None,
                               "artifact": None, "ts": None}},
    }
    save_state(state_path, state)
    print(f"OK: agent {args.agent_id} 预算已建 {state_path}")


def refill_bucket(state):
    now = time.time()
    elapsed = now - state["bucket_last_refill"]
    if elapsed > 0:
        state["bucket_tokens"] = min(
            float(state["bucket_capacity"]),
            state["bucket_tokens"] + elapsed * state["bucket_rate"])
        state["bucket_last_refill"] = now


def cmd_advance(args):
    state = load_state(args.state)
    if args.stage != "budget":
        print(f"ERROR: 未知阶段 {args.stage}", file=sys.stderr)
        sys.exit(2)
    cur = state["stages"]["budget"]
    if cur["status"] == "done":
        fail("预算阶段已完成，不许重复推进")
    if not args.artifact:
        fail("预算阶段缺产出物 --artifact")
    if not artifact_exists(args.state, args.artifact):
        fail(f"预算文件不存在：{args.artifact}")
    cur.update({"status": "done", "actor": args.actor,
                "artifact": args.artifact, "ts": now_iso()})
    save_state(args.state, state)
    print(f"OK: budget done by {args.actor}")


def cmd_charge(args):
    state = load_state(args.state)
    if state["stages"]["budget"]["status"] != "done":
        fail("无预算不许调用：先完成 budget 阶段")
    now = time.time()
    # 熔断冷却期
    if state["breaker_tripped_until"] > now:
        if args.high_risk:
            state["manual_reviews"].append(
                {"actor": args.actor, "amount": args.amount,
                 "reason": "熔断冷却期高风险任务转人工", "ts": now_iso()})
            save_state(args.state, state)
        fail("通道熔断冷却期内，调用必拒")
    # token bucket
    refill_bucket(state)
    if args.amount > state["bucket_tokens"]:
        fail(f"token bucket 余量不足：要 {args.amount}，桶里只有 {state['bucket_tokens']:.1f}")
    # 100% 硬拦截
    if state["used_day"] + args.amount > state["budget_day"]:
        fail(f"日预算 100% 硬拦截：已用 {state['used_day']}，预算 {state['budget_day']}，再调用 {args.amount} 将超限")
    if state["used_month"] + args.amount > state["budget_month"]:
        fail(f"月预算 100% 硬拦截：已用 {state['used_month']}，预算 {state['budget_month']}")
    # 高风险任务在降级区间不自动降级，转人工
    ratio_after = (state["used_day"] + args.amount) / state["budget_day"]
    if args.high_risk and ratio_after >= 0.85:
        state["manual_reviews"].append(
            {"actor": args.actor, "amount": args.amount,
             "reason": "高风险任务在 85% 降级区间，转人工", "ts": now_iso()})
        save_state(args.state, state)
        fail("高风险任务禁自动降级，已记转人工")
    # 记账
    state["bucket_tokens"] -= args.amount
    state["used_day"] += args.amount
    state["used_month"] += args.amount
    state["charges"].append(
        {"actor": args.actor, "amount": args.amount, "model": args.model,
         "high_risk": bool(args.high_risk), "failed": bool(args.failed),
         "channel": state["channel"], "ts": now_iso()})
    # 熔断计数
    if args.failed:
        state["consecutive_failures"] += 1
        if state["consecutive_failures"] >= state["breaker_failures"]:
            state["breaker_tripped_until"] = now + state["cooldown"]
            print(f"NOTE: 熔断器已触发，冷却 {state['cooldown']} 秒")
    else:
        state["consecutive_failures"] = 0
    # 阈值记录
    ratio = state["used_day"] / state["budget_day"]
    if ratio >= 0.85:
        state["degrades"].append(
            {"ratio": round(ratio, 3), "ts": now_iso()})
        print(f"NOTE: 用量 {ratio:.0%}，已记限流降级")
    elif ratio >= 0.70:
        state["alerts"].append(
            {"ratio": round(ratio, 3), "ts": now_iso()})
        print(f"NOTE: 用量 {ratio:.0%}，已记告警")
    save_state(args.state, state)
    print(f"OK: charge {args.amount}（{args.model}），日用量 {state['used_day']}/{state['budget_day']}")


def cmd_fallback(args):
    state = load_state(args.state)
    if not state.get("fallback_channel"):
        fail("无备用通道登记，不许切换")
    if args.to != state["fallback_channel"]:
        fail(f"备用通道不符：登记的是 {state['fallback_channel']}，不是 {args.to}")
    state["channel"] = args.to
    save_state(args.state, state)
    print(f"OK: 已切备用通道 {args.to}")


def cmd_usage(args):
    state = load_state(args.state)
    total = sum(c["amount"] for c in state["charges"])
    print(f"agent={state['agent_id']} channel={state['channel']} "
          f"day={state['used_day']}/{state['budget_day']} "
          f"month={state['used_month']}/{state['budget_month']} "
          f"charges_sum={total} alerts={len(state['alerts'])} "
          f"degrades={len(state['degrades'])} manual_reviews={len(state['manual_reviews'])}")


def check_state(state, state_path):
    problems = []
    if state["stages"]["budget"]["status"] == "done":
        art = state["stages"]["budget"].get("artifact")
        if not art or not artifact_exists(state_path, art):
            problems.append(f"预算文件缺失：{art}")
    total = sum(c["amount"] for c in state["charges"])
    if total != state["used_day"]:
        problems.append(f"用量与记账不一致：charges 合计 {total}，used_day {state['used_day']}")
    if state["used_day"] > state["budget_day"]:
        problems.append(f"日用量超预算：{state['used_day']} > {state['budget_day']}")
    if state["used_month"] > state["budget_month"]:
        problems.append(f"月用量超预算：{state['used_month']} > {state['budget_month']}")
    if state["used_day"] / max(state["budget_day"], 1) >= 0.70 and not state["alerts"] and not state["degrades"]:
        problems.append("用量≥70% 但无告警/降级记录")
    if state["used_day"] / max(state["budget_day"], 1) >= 0.85 and not state["degrades"]:
        problems.append("用量≥85% 但无降级记录")
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
    print(f"agent={state['agent_id']} channel={state['channel']} "
          f"day={state['used_day']}/{state['budget_day']} "
          f"bucket={state['bucket_tokens']:.1f}/{state['bucket_capacity']} "
          f"failures={state['consecutive_failures']}")
    for s in STAGES:
        st = state["stages"][s]
        mark = "DONE " if st["status"] == "done" else ".... "
        extra = f" by {st['actor']}" if st["actor"] else ""
        print(f"  {mark}{s}{extra}")


def main():
    ap = argparse.ArgumentParser(description="成本与额度 SOP 门禁")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--agent-id", required=True)
    p.add_argument("--workdir", required=True)
    p.add_argument("--budget-day", type=int, required=True)
    p.add_argument("--budget-month", type=int, required=True)
    p.add_argument("--bucket-capacity", type=int, default=1000)
    p.add_argument("--bucket-rate", type=float, default=100.0)
    p.add_argument("--breaker-failures", type=int, default=3)
    p.add_argument("--cooldown", type=float, default=60.0)
    p.add_argument("--fallback", default="")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("advance")
    p.add_argument("--state", required=True)
    p.add_argument("--stage", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--artifact", required=True)
    p.set_defaults(fn=cmd_advance)
    p = sub.add_parser("charge")
    p.add_argument("--state", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--amount", type=int, required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--high-risk", action="store_true")
    p.add_argument("--failed", action="store_true")
    p.set_defaults(fn=cmd_charge)
    p = sub.add_parser("fallback")
    p.add_argument("--state", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--to", required=True)
    p.set_defaults(fn=cmd_fallback)
    p = sub.add_parser("usage")
    p.add_argument("--state", required=True)
    p.set_defaults(fn=cmd_usage)
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
