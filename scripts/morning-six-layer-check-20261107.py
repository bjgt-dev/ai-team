#!/usr/bin/env python3
"""Morning six-layer check (2026-11-07, MEMORY.md 汇报先全局后单件).

Rule (memory-only until now): before the morning report, the six layers
must all be covered -- workers, queue, credentials ledger, gateway
health, deadline risk, commercial. The latest morning report on file
(20261105) is a single-topic gateway note, i.e. the rule had no check.
This checker reads the latest *morning-report* file in state/outbox
(or --file) and FAILs listing any missing layer. Exit 0/1/2.
"""
import argparse, glob, os, sys

BASE = os.environ.get("AI_TEAM", "/home/ubuntu/ai-team")
OUTBOX = os.path.join(BASE, "state", "outbox")
LAYERS = {
    "workers": ("工人", "worker"),
    "queue": ("队列", "queue"),
    "credentials-ledger": ("凭据", "credential", "台账"),
    "gateway-health": ("网关", "gateway"),
    "deadline-risk": ("截止", "deadline", "超期", "到期"),
    "commercial": ("商业", "commercial"),
}


def check_layers(text):
    low = text.lower()
    return [name for name, keys in LAYERS.items()
            if not any(k.lower() in low for k in keys)]


def latest_report(outbox):
    cands = sorted(glob.glob(os.path.join(outbox, "*morning-report*")))
    return cands[-1] if cands else None


def self_test():
    ok = True

    def ck(cond, label):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + label)
        ok = ok and cond

    full = ("工人 3 台在线；队列 5 READY；凭据台账已对；网关健康；"
            "截止风险：无超期；商业：3 件已验。")
    ck(check_layers(full) == [], "all six layers present -> PASS")
    ck(check_layers("网关健康") == ["workers", "queue", "credentials-ledger",
                                    "deadline-risk", "commercial"],
       "single-topic report -> lists 5 missing layers")
    ck(check_layers("") == list(LAYERS), "empty report -> all missing")
    print("SELF-TEST " + ("ALL PASS" if ok else "FAILURES PRESENT"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--file", help="morning report file to check")
    ap.add_argument("--outbox", default=OUTBOX)
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    path = args.file or latest_report(args.outbox)
    if not path or not os.path.isfile(path):
        print("SKIP no morning report found in %s" % args.outbox)
        return 2
    text = open(path, errors="replace").read()
    missing = check_layers(text)
    if missing:
        print("FAIL %s missing layers: %s" % (path, ", ".join(missing)))
        return 1
    print("PASS %s covers all six layers" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
