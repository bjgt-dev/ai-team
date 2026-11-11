#!/usr/bin/env python3
"""Registration-email guard (2026-11-07, MEMORY.md 注册邮箱统一口径).

Rule (memory-only until now): every new account registration uses
registration@example.com; main mailbox main@example.com stays login-only;
proxy@example.com stays reverse-proxy-only. Any other mailbox used for
registration must be decided by the chair, never swapped in silently.
This guard lints hub code/config (scripts/*.py|*.sh, state/*.json) for
email addresses and FAILs on any address outside the allowlist, so a
hard-coded wrong registration mailbox is caught before it is used.
Vendor support addresses that are not accounts are allowlisted explicitly.
Exit 0 PASS, 1 FAIL, 2 SKIP (scan root missing).
"""
import argparse, os, re, sys

BASE = os.environ.get("AI_TEAM", "/home/ubuntu/ai-team")
ALLOW = {
    "registration@example.com",          # the one registration mailbox
    "main@example.com",     # main mailbox, login only
    "proxy@example.com",         # reverse proxy only
    "pool@example.com",     # Muse pool account (already registered)
    "support@example.com",    # vendor support contact, not an account
}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SCAN_GLOBS = (("scripts", (".py", ".sh")), ("state", (".json",)))


def scan(root):
    hits = []
    for sub, exts in SCAN_GLOBS:
        d = os.path.join(root, sub)
        if not os.path.isdir(d):
            continue
        for dirpath, _, files in os.walk(d):
            for fn in sorted(files):
                if not fn.endswith(exts) or ".bak" in fn:
                    continue
                path = os.path.join(dirpath, fn)
                try:
                    text = open(path, errors="replace").read()
                except OSError:
                    continue
                for i, line in enumerate(text.splitlines(), 1):
                    for m in EMAIL_RE.findall(line):
                        if m.lower() not in ALLOW:
                            hits.append((path, i, m, line.strip()[:160]))
    return hits


def self_test():
    import tempfile
    ok = True

    def ck(cond, label):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + label)
        ok = ok and cond

    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "scripts"))
        os.makedirs(os.path.join(td, "state"))
        with open(os.path.join(td, "scripts", "a.py"), "w") as f:
            f.write('EMAIL = "registration@example.com"  # register\n')
            f.write('LOGIN = "main@example.com"\n')
        with open(os.path.join(td, "state", "b.json"), "w") as f:
            f.write('{"proxy": "proxy@example.com"}')
        ck(scan(td) == [], "allowlisted mailboxes only -> PASS")
        bad_addr = "someone-else@" + "example.com"  # built at runtime so
        # this file itself holds no literal unlisted mailbox for the live scan
        with open(os.path.join(td, "scripts", "a.py"), "a") as f:
            f.write('OTHER = "%s"  # register\n' % bad_addr)
        hits = scan(td)
        ck(len(hits) == 1 and hits[0][2] == bad_addr,
           "unlisted mailbox -> FAIL with file:line")
        with open(os.path.join(td, "scripts", "a.py"), "a") as f:
            f.write('# see support@example.com for support\n')
        hits = scan(td)
        ck(len(hits) == 1, "vendor support address allowlisted, still 1 hit")
        ck(scan(os.path.join(td, "nope")) == [], "missing root -> empty (caller maps to SKIP)")
    print("SELF-TEST " + ("ALL PASS" if ok else "FAILURES PRESENT"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--root", default=BASE)
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if not os.path.isdir(args.root):
        print("SKIP root missing: %s" % args.root)
        return 2
    hits = scan(args.root)
    if hits:
        for path, line_no, addr, line in hits:
            print("FAIL %s:%d unlisted mailbox %s :: %s" % (path, line_no, addr, line))
        print("FAIL %d unlisted mailbox(es); registration mailbox must be "
              "registration@example.com unless the chair decides otherwise" % len(hits))
        return 1
    print("PASS no unlisted mailboxes in hub code/config (allowlist %d)" % len(ALLOW))
    return 0


if __name__ == "__main__":
    sys.exit(main())
