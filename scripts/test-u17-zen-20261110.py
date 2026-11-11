#!/usr/bin/env python3
"""U-17 tests (2026-11-10): Zen probe timeout 300s + failure -> outbox alert.

Run on hub: python3 scripts/test-u17-zen-20261110.py
Exit 0 = all pass. Does NOT call the live model (live probe is a separate
manual step: python3 scripts/brain_canary.py --zen-only).
"""
import importlib.util
import inspect
import os
import sys
import tempfile

BASE = "/home/ubuntu/ai-team"
SCRIPTS = os.path.join(BASE, "scripts")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    bc = load("brain_canary", os.path.join(SCRIPTS, "brain_canary.py"))
    fails = []

    def check(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        if not cond:
            fails.append(label)

    sig = inspect.signature(bc.opencode_text)
    timeout_default = sig.parameters["timeout"].default
    check("opencode_text default timeout==300 (was 180): %s" % timeout_default,
          timeout_default == 300)
    check("zen_check uses 300s timeout",
          "300" in inspect.getsource(bc.zen_check))

    # --- simulated Zen failure MUST raise an outbox alert (canary) ---
    tmp = tempfile.mkdtemp(prefix="u17test-")
    bc.OUTBOX = os.path.join(tmp, "outbox")
    fake_fail = {"status": "FAILED", "detail": "rc=124 marker=False",
                 "answer": "", "elapsed_s": 300.1,
                 "channel": "opencode/deepseek-v4.1-flash-free",
                 "last_checked": "2026-11-10T19:30:00+08:00"}
    path = bc.alert_zen_failure(fake_fail)
    check("simulated Zen FAILED writes outbox alert file", path is not None and os.path.exists(path))
    if path and os.path.exists(path):
        body = open(path).read()
        check("alert file names model + rc=124 + elapsed",
              "deepseek-v4.1-flash-free" in body and "124" in body and "300.1" in body)
    fake_ok = dict(fake_fail, status="HEALTHY", detail="rc=0 marker=True", elapsed_s=12.3)
    path2 = bc.alert_zen_failure(fake_ok)
    check("simulated Zen HEALTHY writes no alert", path2 is None)

    print("U17-TEST %s (%d failures)" % ("PASS" if not fails else "FAIL", len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
