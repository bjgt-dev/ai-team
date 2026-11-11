#!/usr/bin/env python3
"""CF 22-node preserve guard (2026-11-07, MEMORY.md CF本地优选22节点).

Rule (memory-only until now): the 22 chairman-picked IPs must be kept
verbatim through any trim/convert/delist -- never swapped for generic
pool IPs. Pinned list: state/cf22-pinned-ips.txt (22 unique IPv4,
taken from the local bj​wt22-slim-sub.txt, verified 22 unique).
  * pinned file must hold exactly 22 unique valid IPv4, else FAIL.
  * --check FILE: every pinned IP must appear in FILE (missing -> FAIL);
    any other IPv4 found in FILE is reported as UNPINNED (generic-pool
    substitution is a WARN that fails with --strict).
Exit 0 PASS, 1 FAIL, 2 SKIP (pinned file missing).
"""
import argparse, os, re, sys

BASE = os.environ.get("AI_TEAM", "/home/ubuntu/ai-team")
PINNED = os.path.join(BASE, "state", "cf22-pinned-ips.txt")
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
EXPECTED = 22


def valid_ip(ip):
    parts = ip.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def load_pinned(path):
    try:
        with open(path) as f:
            ips = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    except OSError:
        return None, "pinned file missing: %s" % path
    bad = [ip for ip in ips if not valid_ip(ip)]
    if bad:
        return None, "invalid IPs in pinned file: %s" % bad
    if len(ips) != EXPECTED or len(set(ips)) != EXPECTED:
        return None, "pinned file must hold exactly %d unique IPs, has %d (%d unique)" % (
            EXPECTED, len(ips), len(set(ips)))
    return ips, ""


def check_file(pinned, text):
    found = set(IP_RE.findall(text))
    missing = [ip for ip in pinned if ip not in found]
    unpinned = sorted(ip for ip in found if valid_ip(ip) and ip not in pinned)
    return missing, unpinned


def self_test():
    import tempfile
    ok = True

    def ck(cond, label):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + label)
        ok = ok and cond

    ips = ["10.0.0.%d" % i for i in range(1, 23)]
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "pinned.txt")
        with open(p, "w") as f:
            f.write("\n".join(ips) + "\n")
        got, why = load_pinned(p)
        ck(got == ips, "22 unique valid IPs load clean")
        sub = os.path.join(td, "sub.txt")
        with open(sub, "w") as f:
            f.write("vless://x@" + "@".join(ips) + "\n")
        missing, unpinned = check_file(got, open(sub).read())
        ck(missing == [] and unpinned == [], "subscription keeps all 22 -> PASS")
        with open(sub, "w") as f:
            f.write("vless://x@" + "@".join(ips[:-1]) + "\n")
        missing, _ = check_file(got, open(sub).read())
        ck(missing == ["10.0.0.22"], "one IP dropped -> FAIL lists it")
        with open(sub, "w") as f:
            f.write("vless://x@" + "@".join(ips) + " 9.9.9.9\n")
        _, unpinned = check_file(got, open(sub).read())
        ck(unpinned == ["9.9.9.9"], "generic-pool IP added -> UNPINNED warn")
        with open(p, "w") as f:
            f.write("\n".join(ips[:21]) + "\n")
        got, _ = load_pinned(p)
        ck(got is None, "21 IPs in pinned file -> FAIL")
        ck(load_pinned(os.path.join(td, "nope.txt"))[0] is None, "missing pinned -> SKIP")
    print("SELF-TEST " + ("ALL PASS" if ok else "FAILURES PRESENT"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--pinned", default=PINNED)
    ap.add_argument("--check", help="subscription file to verify")
    ap.add_argument("--strict", action="store_true",
                    help="unpinned IPs also fail")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    pinned, why = load_pinned(args.pinned)
    if pinned is None:
        print("SKIP %s" % why)
        return 2
    if not args.check:
        print("PASS pinned list holds exactly 22 unique IPs (no --check target given)")
        return 0
    try:
        text = open(args.check).read()
    except OSError as e:
        print("SKIP cannot read %s: %s" % (args.check, e))
        return 2
    missing, unpinned = check_file(pinned, text)
    if missing:
        print("FAIL missing pinned IPs in %s: %s" % (args.check, ", ".join(missing)))
        return 1
    if unpinned:
        print("WARN unpinned IPs present (possible generic-pool substitution): %s"
              % ", ".join(unpinned))
        if args.strict:
            return 1
    print("PASS all 22 pinned IPs present in %s" % args.check)
    return 0


if __name__ == "__main__":
    sys.exit(main())
