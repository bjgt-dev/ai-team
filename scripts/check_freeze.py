#!/usr/bin/env python3
# Code-freeze checker (2026-10-20 supervisor item 4): recompute sha256 of the
# gate chain files and compare with state/code_freeze.json. Any drift = exit 1.
# Usage: check_freeze.py [--manifest PATH]
import hashlib, json, os, sys

DEFAULT = "/home/ubuntu/ai-team/state/code_freeze.json"

def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()

def main():
    manifest_path = DEFAULT
    if "--manifest" in sys.argv:
        manifest_path = sys.argv[sys.argv.index("--manifest") + 1]
    m = json.load(open(manifest_path))
    bad = []
    for path, want in m["files"].items():
        try:
            got = sha(path)
        except Exception as e:
            bad.append("%s unreadable: %s" % (path, e)); continue
        if got != want:
            bad.append("%s hash drift" % path)
    if bad:
        print("FREEZE_VIOLATION: " + "; ".join(bad))
        return 1
    print("FREEZE_OK %d files match %s" % (len(m["files"]), manifest_path))
    return 0

if __name__ == "__main__":
    sys.exit(main())
