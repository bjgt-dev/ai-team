#!/usr/bin/env python3
"""Canonical scripts/ entry for the CF Workers AI neuron guard (2026-11-07).

Implementation: state/cf-workers-ai-20261105/cf-neuron-guard.py
(channel 29, 5 models, cron every 30 min -- already scheduled and
running). Gap was location only: the guard lived under state/, not
scripts/. This wrapper forwards argv/stdout/stderr/exit code
unchanged; it installs no new cron line (the existing 30-min line
keeps pointing at the state/ path until a chair-approved change).
"""
import os
import subprocess
import sys

if __name__ == "__main__":
    base = os.environ.get("AI_TEAM_BASE", "/home/ubuntu/ai-team")
    impl = os.path.join(base, "state", "cf-workers-ai-20261105",
                        "cf-neuron-guard.py")
    if not os.path.isfile(impl):
        print("cf neuron guard implementation missing: %s" % impl, file=sys.stderr)
        sys.exit(2)
    sys.exit(subprocess.call([sys.executable, impl] + sys.argv[1:]))
