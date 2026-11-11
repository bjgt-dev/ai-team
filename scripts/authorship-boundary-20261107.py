#!/usr/bin/env python3
"""authorship-boundary-20261107.py -- Thorsten Ball Rule 14 别让 Agent 替人写东西.

SOP status: 拟议口径待董事长拍板 (Agent 可写: 调试步骤/事故报告/技术
文档/代码注释; 不可写须人写或人批: 用户-facing 文案/营销/对外发布/
带语气与判断的文章). The boundary is a CHAIRMAN decision and this script
deliberately does NOT invent it: it lives in
state/authorship-boundary-20261107.json
{"agent_may_write": [...], "human_only": [...]}. Until the chairman sets
it, the config does not exist and this gate exits 0 with NOT-CONFIGURED.

Usage: authorship-boundary-20261107.py --kind KIND [--approved]
  KIND is the writing kind, e.g. debug-steps / incident-report / tech-doc /
  code-comment / user-facing-copy / marketing / external-release / opinion-piece.
  exit 0: kind is agent-writable, or human-only kind with --approved.
  exit 2: human-only kind without approval -> HUMAN MUST WRITE OR APPROVE.
  exit 3: tool error.
"""
import argparse
import json
import os
import sys

DEFAULT_CONFIG = "/home/ubuntu/ai-team/state/authorship-boundary-20261107.json"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Rule 14 authorship boundary gate (config-driven)")
    ap.add_argument("--kind", required=True, help="writing kind")
    ap.add_argument("--approved", action="store_true", help="human approved this text")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    args = ap.parse_args(argv)
    if not os.path.exists(args.config):
        print("NOT-CONFIGURED: %s does not exist -- boundary not set by chairman yet, no enforcement" % args.config)
        return 0
    try:
        with open(args.config, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as exc:
        print("TOOL-ERROR: cannot read config %s: %s" % (args.config, exc))
        return 3
    if not isinstance(cfg, dict):
        print("TOOL-ERROR: config is not a JSON object")
        return 3
    human_only = cfg.get("human_only") or []
    agent_may = cfg.get("agent_may_write") or []
    if args.kind in human_only and not args.approved:
        print("HUMAN-REQUIRED: %s is human-only (needs a human author or approver)" % args.kind)
        return 2
    if args.kind in agent_may or args.approved or args.kind not in human_only:
        print("PASS: %s (approved=%s)" % (args.kind, args.approved))
        return 0
    print("PASS: %s" % args.kind)
    return 0


if __name__ == "__main__":
    sys.exit(main())
