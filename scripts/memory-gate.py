#!/usr/bin/env python3
"""memory-gate.py — 记忆与知识库 SOP 门禁（SOP v1.0，2026-11-10 董事长令）。

写入门禁五检查（值得记→去重→冲突消解→有效期→脱敏）；
操作 ADD/CONFIRM/SUPERSEDE/RETRACT/EXPIRE/DELETE；
冲突不靠“最新获胜”，先比证据权威性；分层 TTL；定期整合；KB 改动 PR 审核。
违规一律 exit 1，打印 VIOLATION，绝不静默放行。

用法：
  init --kb-id <id> --workdir <dir>
  propose --state <state.json> --entry-id <id> --operation <OP>
          --actor <提议人> --content <文件> --tier <session|agreement|architecture>
          --evidence <high|medium|low> [--key <去重键>] [--age-days <N>]
  check5 --state <state.json> --entry-id <id>
  resolve --state <state.json> --entry-id <id> --decision <keep_new|keep_old|keep_both_pending>
  apply --state <state.json> --entry-id <id> --reviewer <审核人>
  consolidate --state <state.json> --actor <席位> --artifact <整合记录>
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

OPERATIONS = ["ADD", "CONFIRM", "SUPERSEDE", "RETRACT", "EXPIRE", "DELETE"]
TIERS = {"session": 1, "agreement": 90, "architecture": 3650}
EVIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}
SECRET_PATTERNS = [
    re.compile(r"(?i)password\s*[:=]"),
    re.compile(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*\S+"),
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)\b\d{17}[\dXx]\b"),  # 疑似身份证号
]


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
    state_path = os.path.join(args.workdir, "memory-sop-state.json")
    if os.path.exists(state_path):
        print(f"ERROR: state 已存在 {state_path}，不许覆盖", file=sys.stderr)
        sys.exit(2)
    state = {
        "kb_id": args.kb_id,
        "created_at": now_iso(),
        "entries": {},
        "keys": {},
        "consolidations": [],
    }
    save_state(state_path, state)
    print(f"OK: 知识库 {args.kb_id} 已建 {state_path}")


def cmd_propose(args):
    state = load_state(args.state)
    if args.operation not in OPERATIONS:
        print(f"ERROR: 未知操作 {args.operation}", file=sys.stderr)
        sys.exit(2)
    if args.entry_id in state["entries"]:
        fail(f"条目已存在：{args.entry_id}，不许重复 propose")
    if args.tier not in TIERS:
        print(f"ERROR: 未知层 {args.tier}", file=sys.stderr)
        sys.exit(2)
    if args.evidence not in EVIDENCE_RANK:
        print(f"ERROR: 未知证据等级 {args.evidence}", file=sys.stderr)
        sys.exit(2)
    if not os.path.exists(args.content if os.path.isabs(args.content)
                          else os.path.join(base_dir(args.state), args.content)):
        fail(f"内容文件不存在：{args.content}")
    # SUPERSEDE/RETRACT/EXPIRE/CONFIRM 须有所指旧条目（key 指向已应用条目）
    target = None
    if args.operation in ("SUPERSEDE", "RETRACT", "EXPIRE", "CONFIRM"):
        if not args.key or args.key not in state["keys"]:
            fail(f"{args.operation} 须有所指的已应用条目 key：{args.key}")
        target = state["keys"][args.key]
    state["entries"][args.entry_id] = {
        "operation": args.operation,
        "proposer": args.actor,
        "reviewer": None,
        "content": args.content,
        "tier": args.tier,
        "ttl_days": TIERS[args.tier],
        "evidence": args.evidence,
        "key": args.key or args.entry_id,
        "target": target,
        "created_ts": time.time() - (args.age_days or 0) * 86400,
        "status": "proposed",
        "checks": {},
        "conflict": None,
        "resolution": None,
    }
    save_state(args.state, state)
    print(f"OK: 条目 {args.entry_id} 已提议（{args.operation}）")


def cmd_check5(args):
    state = load_state(args.state)
    e = state["entries"].get(args.entry_id)
    if not e:
        print(f"ERROR: 条目不存在：{args.entry_id}", file=sys.stderr)
        sys.exit(2)
    if e["status"] != "proposed":
        fail(f"条目 {args.entry_id} 状态为 {e['status']}，不许重复 check5")
    content = read_content(args.state, e["content"])
    checks = {}
    # 1. 值得记吗：内容非空且长度 ≥10 字
    checks["worth"] = len(content.strip()) >= 10
    if not checks["worth"]:
        fail("检查 1 不过：内容过短，不值得记")
    # 2. 去重：ADD 时同 key 已有已应用条目即重复
    if e["operation"] == "ADD" and e["key"] in state["keys"]:
        checks["dedupe"] = False
        fail(f"检查 2 不过：key {e['key']} 已有条目，重复写入；应走 CONFIRM 或丢弃")
    checks["dedupe"] = True
    # 3. 冲突：SUPERSEDE 时比证据权威性
    if e["operation"] == "SUPERSEDE" and e.get("target"):
        old = state["entries"][e["target"]]
        if EVIDENCE_RANK[e["evidence"]] < EVIDENCE_RANK[old["evidence"]]:
            e["conflict"] = {"with": e["target"],
                             "reason": "新证据弱于旧证据"}
            checks["conflict"] = False
            save_state(args.state, state)
            fail("检查 3 不过：新证据弱于旧证据，冲突须 resolve（keep_old 或 keep_both_pending）")
        checks["conflict"] = True
    else:
        checks["conflict"] = True
    # 4. 有效期：tier 已定 TTL
    checks["ttl"] = e["ttl_days"] > 0
    # 5. 脱敏：内容含敏感模式即不过
    for pat in SECRET_PATTERNS:
        if pat.search(content):
            checks["desensitize"] = False
            fail(f"检查 5 不过：内容含敏感信息（命中 {pat.pattern}），只记位置指针，不许写入原文")
    checks["desensitize"] = True
    e["checks"] = checks
    e["status"] = "checked"
    save_state(args.state, state)
    print(f"OK: 条目 {args.entry_id} 五检查通过")


def cmd_resolve(args):
    state = load_state(args.state)
    e = state["entries"].get(args.entry_id)
    if not e:
        print(f"ERROR: 条目不存在：{args.entry_id}", file=sys.stderr)
        sys.exit(2)
    if not e.get("conflict"):
        fail(f"条目 {args.entry_id} 无冲突，不许 resolve")
    if args.decision == "keep_new":
        if EVIDENCE_RANK[e["evidence"]] < EVIDENCE_RANK[
                state["entries"][e["conflict"]["with"]]["evidence"]]:
            fail("新证据弱于旧证据，不许 keep_new；只能 keep_old 或 keep_both_pending")
    if args.decision not in ("keep_new", "keep_old", "keep_both_pending"):
        print(f"ERROR: 未知决议 {args.decision}", file=sys.stderr)
        sys.exit(2)
    e["resolution"] = args.decision
    e["conflict"] = None
    e["status"] = "checked"
    save_state(args.state, state)
    print(f"OK: 冲突已消解（{args.decision}）")


def cmd_apply(args):
    state = load_state(args.state)
    e = state["entries"].get(args.entry_id)
    if not e:
        print(f"ERROR: 条目不存在：{args.entry_id}", file=sys.stderr)
        sys.exit(2)
    if e["status"] != "checked":
        fail(f"条目 {args.entry_id} 未过五检查（状态 {e['status']}），不许 apply")
    if e.get("conflict"):
        fail(f"条目 {args.entry_id} 冲突未消解，不许 apply")
    if args.reviewer == e["proposer"]:
        fail("审核人不得是提议人")
    # 过期检查：创建时间 + TTL 已过即过期
    if time.time() > e["created_ts"] + e["ttl_days"] * 86400:
        fail(f"条目 {args.entry_id} 已过期（TTL {e['ttl_days']} 天），不许 apply")
    e["reviewer"] = args.reviewer
    e["status"] = "applied"
    if e["operation"] == "ADD":
        state["keys"][e["key"]] = args.entry_id
    elif e["operation"] == "SUPERSEDE" and e.get("target"):
        state["entries"][e["target"]]["status"] = "superseded"
        state["keys"][e["key"]] = args.entry_id
    elif e["operation"] == "RETRACT" and e.get("target"):
        state["entries"][e["target"]]["status"] = "retracted"
    elif e["operation"] == "EXPIRE" and e.get("target"):
        state["entries"][e["target"]]["status"] = "expired"
    elif e["operation"] == "DELETE" and e.get("target"):
        state["entries"][e["target"]]["status"] = "deleted"
    save_state(args.state, state)
    print(f"OK: 条目 {args.entry_id} 已应用（{e['operation']}），审核人 {args.reviewer}")


def cmd_consolidate(args):
    state = load_state(args.state)
    if not os.path.exists(args.artifact if os.path.isabs(args.artifact)
                          else os.path.join(base_dir(args.state), args.artifact)):
        fail(f"整合记录不存在：{args.artifact}")
    now = time.time()
    expired = [eid for eid, e in state["entries"].items()
               if e["status"] == "applied"
               and now > e["created_ts"] + e["ttl_days"] * 86400]
    for eid in expired:
        state["entries"][eid]["status"] = "expired"
    state["consolidations"].append(
        {"actor": args.actor, "artifact": args.artifact,
         "expired": expired, "ts": now_iso()})
    save_state(args.state, state)
    print(f"OK: 整合完成，到期 {len(expired)} 条：{expired}")


def check_state(state, state_path):
    problems = []
    for eid, e in state["entries"].items():
        if e["status"] == "applied":
            if not e.get("reviewer"):
                problems.append(f"{eid} 已应用但无审核人")
            if e["reviewer"] == e["proposer"]:
                problems.append(f"{eid} 审核人就是提议人")
            if e.get("conflict"):
                problems.append(f"{eid} 已应用但冲突未消解")
            if time.time() > e["created_ts"] + e["ttl_days"] * 86400:
                problems.append(f"{eid} 已过期但仍标记 applied")
            try:
                content = read_content(state_path, e["content"])
            except OSError:
                problems.append(f"{eid} 内容文件缺失：{e['content']}")
                continue
            for pat in SECRET_PATTERNS:
                if pat.search(content):
                    problems.append(f"{eid} 内容含敏感信息")
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
    print(f"kb={state['kb_id']} entries={len(state['entries'])} "
          f"consolidations={len(state['consolidations'])}")
    for eid, e in state["entries"].items():
        print(f"  {e['status']:10s} {eid} ({e['operation']}/{e['tier']}/证据{e['evidence']})")


def main():
    ap = argparse.ArgumentParser(description="记忆与知识库 SOP 门禁")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--kb-id", required=True)
    p.add_argument("--workdir", required=True)
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("propose")
    p.add_argument("--state", required=True)
    p.add_argument("--entry-id", required=True)
    p.add_argument("--operation", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--content", required=True)
    p.add_argument("--tier", required=True)
    p.add_argument("--evidence", required=True)
    p.add_argument("--key", default="")
    p.add_argument("--age-days", type=int, default=0)
    p.set_defaults(fn=cmd_propose)
    p = sub.add_parser("check5")
    p.add_argument("--state", required=True)
    p.add_argument("--entry-id", required=True)
    p.set_defaults(fn=cmd_check5)
    p = sub.add_parser("resolve")
    p.add_argument("--state", required=True)
    p.add_argument("--entry-id", required=True)
    p.add_argument("--decision", required=True)
    p.set_defaults(fn=cmd_resolve)
    p = sub.add_parser("apply")
    p.add_argument("--state", required=True)
    p.add_argument("--entry-id", required=True)
    p.add_argument("--reviewer", required=True)
    p.set_defaults(fn=cmd_apply)
    p = sub.add_parser("consolidate")
    p.add_argument("--state", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--artifact", required=True)
    p.set_defaults(fn=cmd_consolidate)
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
