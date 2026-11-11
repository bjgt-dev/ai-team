#!/usr/bin/env python3
"""Canonical scripts/ entry for the SiliconFlow quota guard (2026-11-07).

Implementation: state/siliconflow-20261105/siliconflow-quota-guard.py
(2026-11-05). Gap: no scripts/ entry, no live cron line, and the
SiliconFlow channels themselves are NOT built (key in Secure Vault,
plaintext not readable by the executor -- blocker recorded in
state/siliconflow-20261105/RECEIPT-20261105.md; this wrapper does not
pretend to fix that, it only makes the guard callable from scripts/).
Forwards argv/stdout/stderr/exit code unchanged; installs no cron.
"""
import os
import subprocess
import sys

if __name__ == "__main__":
    base = os.environ.get("AI_TEAM_BASE", "/home/ubuntu/ai-team")
    impl = os.path.join(base, "state", "siliconflow-20261105",
                        "siliconflow-quota-guard.py")
    if not os.path.isfile(impl):
        print("siliconflow guard implementation missing: %s" % impl, file=sys.stderr)
        sys.exit(2)
    sys.exit(subprocess.call([sys.executable, impl] + sys.argv[1:]))
