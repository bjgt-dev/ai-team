#!/usr/bin/env python3
"""sop-mirror-check.py -- cutover51 W6.2: SOP 单向镜像校验.

Local mirror (~/workspace, this sandbox) is READ-ONLY relative to the hub
(~/ai-team). This script rsyncs hub -> local (one direction, --delete only
inside the mirror dirs listed in MIRROR_DIRS) and verifies sha256 of every
mirrored file against the hub copy; any drift (local file newer/different
than hub without a hub-side change) is an alert.
Run daily via cron. It never pushes local -> hub.
Usage: python3 sop-mirror-check.py [--fix]   (without --fix: report only)
"""
import hashlib
import os
import subprocess
import sys

HUB = os.path.expanduser("~/ai-team")
LOCAL = os.path.expanduser("~/workspace")
# Mirror dirs that must be hub->local one-way (docs + scripts that are SOPs).
MIRROR_DIRS = ["docs/sops"]
LOG = os.path.join(LOCAL, "sop-mirror-check.log")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(65536), b""):
            h.update(c)
    return h.hexdigest()


def main():
    fix = "--fix" in sys.argv
    drift = []
    for d in MIRROR_DIRS:
        hub_d, loc_d = os.path.join(HUB, d), os.path.join(LOCAL, d)
        if not os.path.isdir(hub_d):
            print("hub dir missing:", hub_d)
            continue
        for dirpath, _dirs, files in os.walk(hub_d):
            for f in files:
                hp = os.path.join(dirpath, f)
                rel = os.path.relpath(hp, HUB)
                lp = os.path.join(LOCAL, rel)
                if not os.path.exists(lp):
                    drift.append(("missing-local", rel))
                elif sha(hp) != sha(lp):
                    drift.append(("sha-mismatch", rel))
    print("drift=%d" % len(drift))
    for kind, rel in drift:
        print(" ", kind, rel)
    if fix and drift:
        for d in MIRROR_DIRS:
            subprocess.run(["rsync", "-a", "--delete",
                            os.path.join(HUB, d) + "/",
                            os.path.join(LOCAL, d) + "/"],
                           check=False)
        print("rsync hub->local done")
    with open(LOG, "a") as f:
        f.write("drift=%d %s\n" % (len(drift), "; ".join("%s:%s" % d for d in drift[:10])))
    return 1 if drift and not fix else 0


if __name__ == "__main__":
    sys.exit(main())
