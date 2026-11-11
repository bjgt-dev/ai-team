#!/usr/bin/env python3
"""link-research-registry-20261107.py -- 链接深挖 SOP (link-research-sop.md) 登记表机制.

Before this script the SOP's registry existed only as prose ("登记表:
state/skills-registry.md 与免费渠道总表") with no enforcement of its
own rules: every link must end in exactly one of three terminal states
(已验/不做/待验), triage must precede deep research (先筛后挖), deep
research has a 1-hour timebox, and failures must be recorded with cause
and boundary. This script is that registry:
  add --url U --source S [--relevance commercial|infra|none]
      triage gate: relevance=none -> recorded as 不做 (not worth digging)
      with reason; otherwise state=待验 with a 1h timebox deadline.
  settle --url U --state verified|not-doing [--evidence PATH] [--reason R]
      verified requires --evidence (a file path that exists);
      not-doing requires --reason. Sets terminal state + timestamp.
  fail --url U --reason R --boundary B
      records a verification failure (cause + boundary conditions);
      state stays 待验 only if a next action + deadline are given,
      otherwise it becomes 不做.
  audit
      flags: 待验 entries past their timebox/deadline with no next
      action; entries with no terminal state and no deadline; verified
      entries whose evidence file is gone. Exit 1 if any flag.
  list [--state S]
      print entries.

Registry file: state/link-research-registry-20261107.jsonl (one JSON per
line). Exit: 0 ok / 1 audit flags or not-found / 2 tool error.
No network calls: star/license checks are recorded from the operator's
input (SOP: 只认 GitHub API 实查数 -- the operator pastes the实查 number;
this script stores it, it does not fetch it).
"""
import argparse
import datetime
import json
import os
import sys

DEFAULT_REGISTRY = "/home/ubuntu/ai-team/state/link-research-registry-20261107.jsonl"
STATES = {"pending", "verified", "not-doing"}
TIMEBOX_MINUTES = 60


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _ts(dt=None):
    return (_now() if dt is None else dt).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load(path):
    entries = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    entries.append(json.loads(line))
    return entries


def _save(path, entries):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _find(entries, url):
    for e in entries:
        if e.get("url") == url:
            return e
    return None


def cmd_add(args):
    entries = _load(args.registry)
    if _find(entries, args.url):
        print("TOOL-ERROR: url already in registry: %s" % args.url)
        return 2
    if args.relevance == "none":
        entries.append({"url": args.url, "source": args.source, "ts": _ts(),
                        "state": "not-doing",
                        "reason": "triage: no relation to commercial lines or infra",
                        "relevance": "none"})
        _save(args.registry, entries)
        print("added as not-doing (triage: irrelevant, no deep research started)")
        return 0
    deadline = _ts(_now() + datetime.timedelta(minutes=TIMEBOX_MINUTES))
    entries.append({"url": args.url, "source": args.source, "ts": _ts(),
                    "state": "pending", "relevance": args.relevance,
                    "timebox_deadline": deadline, "stars": args.stars,
                    "next_action": "deep research within 1h timebox",
                    "next_deadline": deadline})
    _save(args.registry, entries)
    print("added as pending, timebox deadline %s" % deadline)
    return 0


def cmd_settle(args):
    entries = _load(args.registry)
    e = _find(entries, args.url)
    if not e:
        print("TOOL-ERROR: url not in registry: %s" % args.url)
        return 2
    if args.state == "verified":
        if not args.evidence or not os.path.exists(args.evidence):
            print("TOOL-ERROR: verified requires --evidence pointing at an existing file")
            return 2
        e["state"] = "verified"
        e["evidence"] = args.evidence
    else:
        if not args.reason:
            print("TOOL-ERROR: not-doing requires --reason")
            return 2
        e["state"] = "not-doing"
        e["reason"] = args.reason
    e["settled_ts"] = _ts()
    _save(args.registry, entries)
    print("settled %s -> %s" % (args.url, e["state"]))
    return 0


def cmd_fail(args):
    entries = _load(args.registry)
    e = _find(entries, args.url)
    if not e:
        print("TOOL-ERROR: url not in registry: %s" % args.url)
        return 2
    failures = e.get("failures", [])
    failures.append({"ts": _ts(), "reason": args.reason, "boundary": args.boundary})
    e["failures"] = failures
    if args.next_action and args.next_deadline:
        e["state"] = "pending"
        e["next_action"] = args.next_action
        e["next_deadline"] = args.next_deadline
    else:
        e["state"] = "not-doing"
        e["reason"] = "verification failed: %s (boundary: %s)" % (args.reason, args.boundary)
    _save(args.registry, entries)
    print("failure recorded for %s -> %s" % (args.url, e["state"]))
    return 0


def cmd_audit(args):
    entries = _load(args.registry)
    flags = []
    now = _ts()
    for e in entries:
        if e.get("state") == "pending":
            if not e.get("next_deadline"):
                flags.append("%s: pending with no deadline" % e["url"])
            elif e["next_deadline"] < now:
                flags.append("%s: pending past deadline %s, no next action taken" % (e["url"], e["next_deadline"]))
        if e.get("state") == "verified" and e.get("evidence") and not os.path.exists(e["evidence"]):
            flags.append("%s: verified but evidence file gone: %s" % (e["url"], e["evidence"]))
        if e.get("state") not in STATES:
            flags.append("%s: unknown state %r (only pending/verified/not-doing allowed)" % (e["url"], e.get("state")))
    if flags:
        print("AUDIT FAIL (%d):" % len(flags))
        for f in flags:
            print("  - %s" % f)
        return 1
    print("AUDIT PASS: %d entries, every link has a terminal state or a live deadline" % len(entries))
    return 0


def cmd_list(args):
    entries = _load(args.registry)
    for e in entries:
        if args.state and e.get("state") != args.state:
            continue
        print("%-10s %-12s %s" % (e.get("state"), e.get("relevance"), e.get("url")))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="link-research registry (link-research-sop.md)")
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--url", required=True)
    a.add_argument("--source", required=True)
    a.add_argument("--relevance", required=True, choices=["commercial", "infra", "none"])
    a.add_argument("--stars", type=int, default=None, help="GitHub stars, API-verified only")
    a.set_defaults(fn=cmd_add)
    s = sub.add_parser("settle")
    s.add_argument("--url", required=True)
    s.add_argument("--state", required=True, choices=["verified", "not-doing"])
    s.add_argument("--evidence", default=None)
    s.add_argument("--reason", default=None)
    s.set_defaults(fn=cmd_settle)
    f = sub.add_parser("fail")
    f.add_argument("--url", required=True)
    f.add_argument("--reason", required=True)
    f.add_argument("--boundary", required=True)
    f.add_argument("--next-action", default=None)
    f.add_argument("--next-deadline", default=None)
    f.set_defaults(fn=cmd_fail)
    au = sub.add_parser("audit")
    au.set_defaults(fn=cmd_audit)
    li = sub.add_parser("list")
    li.add_argument("--state", default=None, choices=sorted(STATES))
    li.set_defaults(fn=cmd_list)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
