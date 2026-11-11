#!/usr/bin/env python3
"""blast-radius-gate-20261107.py -- Thorsten Ball Rule 15 只审高爆炸半径代码.

SOP status: 拟议口径待董事长拍板 (名单 5 个文件以内: 数据库迁移/schema
变更、认证/权限/支付路径、公开 API 契约、中心模块、中央队列/派队器/
verifier 本身). The list is a CHAIRMAN decision and this script deliberately
does NOT invent it: the list lives in state/high-blast-radius-20261107.json
{"paths": [...], "categories": [...]}. Until the chairman sets it, the
config file does not exist and this gate exits 0 with NOT-CONFIGURED
(no enforcement, loudly logged) -- the MECHANISM is landed, the DECISION
is not made for him.

Usage: blast-radius-gate-20261107.py [--config PATH] <changed-file>...
  exit 0: no changed file is high-blast (or no list configured yet).
  exit 2: at least one changed file matches the configured list ->
          HUMAN REVIEW REQUIRED (print which files, which rule).
  exit 3: tool error (config unreadable JSON).
A path matches when it equals a listed path, sits under a listed directory,
or matches a listed category keyword (migration/auth/payment/api/queue/
verifier) in its path.
"""
import argparse
import json
import os
import sys

DEFAULT_CONFIG = "/home/ubuntu/ai-team/state/high-blast-radius-20261107.json"
CATEGORY_KEYWORDS = {
    "migration": ("migrat", "schema"),
    "auth": ("auth", "permission", "login"),
    "payment": ("pay", "stripe", "billing"),
    "public-api": ("api", "openapi"),
    "central": ("central_tasks", "queue_api", "dispatch", "verifier", "pipeline-dispatch"),
}


def load_config(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict):
        raise ValueError("config is not a JSON object")
    return cfg


def matches(changed, cfg):
    hits = []
    for p in cfg.get("paths", []) or []:
        if changed == p or changed.startswith(p.rstrip("/") + "/"):
            hits.append("path:%s" % p)
    for cat in cfg.get("categories", []) or []:
        for kw in CATEGORY_KEYWORDS.get(cat, (cat,)):
            if kw in changed:
                hits.append("category:%s (keyword %s)" % (cat, kw))
    return hits


def main(argv=None):
    ap = argparse.ArgumentParser(description="Rule 15 high-blast-radius gate (config-driven)")
    ap.add_argument("files", nargs="*", help="changed file paths")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except Exception as exc:
        print("TOOL-ERROR: cannot read config %s: %s" % (args.config, exc))
        return 3
    if cfg is None:
        print("NOT-CONFIGURED: %s does not exist -- no high-blast list set by chairman yet, no enforcement" % args.config)
        return 0
    if not (cfg.get("paths") or cfg.get("categories")):
        print("NOT-CONFIGURED: list is empty -- no enforcement until chairman sets the list")
        return 0
    bad = {}
    for f in args.files:
        hits = matches(f, cfg)
        if hits:
            bad[f] = hits
    if bad:
        print("HUMAN-REVIEW-REQUIRED: high-blast-radius change:")
        for f, hits in bad.items():
            print("  - %s (%s)" % (f, "; ".join(hits)))
        return 2
    print("PASS: %d changed file(s), none high-blast" % len(args.files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
