#!/usr/bin/env python3
"""Canonical scripts/ entry for the Bailian quota/expiry guard (2026-11-07).

The guard implementation lives at state/bailian-qwen-20261105/
bailian-quota-guard.py (written 2026-11-05, run by hand, py_compile OK).
Gap: it had NO entry under scripts/ and NO live cron line (daily cron
still a draft pending chair approval -- this wrapper installs no cron
and changes no scheduling decision). This thin wrapper is the scripts/
entry point so the audit can find it and a future approved cron line
can point at scripts/ instead of state/. It forwards argv/stdout/
stderr/exit code unchanged and never touches keys.
"""
import os
import subprocess
import sys

IMPL = "/home/ubuntu/ai-team/state/bailian-qwen-20261105/bailian-quota-guard.py"

if __name__ == "__main__":
    base = os.environ.get("AI_TEAM_BASE", "/home/ubuntu/ai-team")
    impl = os.path.join(base, "state", "bailian-qwen-20261105",
                        "bailian-quota-guard.py")
    if not os.path.isfile(impl):
        print("bailian guard implementation missing: %s" % impl, file=sys.stderr)
        sys.exit(2)
    sys.exit(subprocess.call([sys.executable, impl] + sys.argv[1:]))
