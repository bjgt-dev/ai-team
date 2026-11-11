#!/usr/bin/env python3
"""Tests for script-cron-audit-20261107.py (synthetic fixtures only:
temp script dir + fake crontab text; never touches the real hub crontab
or the real scripts dir). Positive + negative cases."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Hub filenames use hyphens (not importable as modules), so load by path.
_a = _load("script_cron_audit_20261107", "script-cron-audit-20261107.py")
build_watchdog_registry = _a.build_watchdog_registry
check_script = _a.check_script
parse_crontab = _a.parse_crontab
scan_scripts = _a.scan_scripts
watchdog_stem = _a.watchdog_stem


class TestCheckScript(unittest.TestCase):
    def test_good_py_passes_positive(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "good.py"
            p.write_text("x = 1\n")
            self.assertTrue(check_script(p)["compile_ok"])

    def test_broken_py_fails_negative(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.py"
            p.write_text("def broken(:\n")
            r = check_script(p)
            self.assertFalse(r["compile_ok"])
            self.assertIn("SyntaxError", r["compile_err"])

    def test_good_sh_passes_positive(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "good.sh"
            p.write_text("#!/bin/bash\necho hi\n")
            self.assertTrue(check_script(p)["compile_ok"])

    def test_broken_sh_fails_negative(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.sh"
            p.write_text("if true; then\n")
            self.assertFalse(check_script(p)["compile_ok"])


class TestScanScripts(unittest.TestCase):
    def test_bak_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.py").write_text("x=1\n")
            (Path(d) / "a.py.bak-20261101").write_text("x=1\n")
            (Path(d) / "b.sh").write_text("echo hi\n")
            live, bak = scan_scripts(Path(d))
            self.assertEqual([s["name"] for s in live], ["a.py", "b.sh"])
            self.assertEqual(bak, 1)


class TestParseCrontab(unittest.TestCase):
    def test_missing_target_flagged_positive(self):
        active, _ = parse_crontab(
            "*/5 * * * * python3 /home/ubuntu/ai-team/scripts/nope.py "
            ">> /tmp/x.log 2>&1\n")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["missing_targets"],
                         ["/home/ubuntu/ai-team/scripts/nope.py"])

    def test_existing_target_clean_negative(self):
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            f.write(b"x=1\n")
            target = f.name
        try:
            active, _ = parse_crontab(
                "*/5 * * * * python3 %s >> /tmp/x.log 2>&1\n" % target)
            # target is in /tmp, not /home/ubuntu -> regex only matches
            # /home/ubuntu paths, so targets list is empty (not flagged)
            self.assertEqual(active[0]["missing_targets"], [])
        finally:
            os.unlink(target)

    def test_no_log_line_detected(self):
        active, _ = parse_crontab(
            "*/5 * * * * /home/ubuntu/ai-team/scripts/x.sh\n")
        self.assertIsNone(active[0]["log"])

    def test_disabled_lines_skipped(self):
        _, disabled = parse_crontab(
            "# DISABLED-20261102-CHAIR04 2-59/2 * * * * /x/y.py\n"
            "*/5 * * * * /home/ubuntu/ai-team/scripts/x.sh\n")
        self.assertEqual(len(disabled), 1)
        self.assertIn("DISABLED", disabled[0]["tag"])

    def test_unparsed_line_kept(self):
        active, _ = parse_crontab("not a cron line\n")
        self.assertEqual(active[0]["parse"], "UNPARSED")


class TestWatchdogRegistry(unittest.TestCase):
    def test_stem_normalisation_groups_dups(self):
        self.assertEqual(watchdog_stem("supervisor-stall-scan.py"),
                         watchdog_stem("05-supervisor-stall-scan.py"))
        self.assertEqual(watchdog_stem("gateway_canary-20261104.py"),
                         "gateway_canary")

    def test_orphan_and_duplicate_detection(self):
        scripts = [
            {"name": "supervisor-stall-scan.py", "compile_ok": True},
            {"name": "05-supervisor-stall-scan.py", "compile_ok": True},
            {"name": "queue_watch.py", "compile_ok": True},
            {"name": "plain_worker.py", "compile_ok": True},
        ]
        cron = "supervisor-stall-scan.py"
        reg, dups = build_watchdog_registry(scripts, cron)
        names = [w["name"] for w in reg]
        self.assertNotIn("plain_worker.py", names)  # not watchdog-ish
        orphans = [w["name"] for w in reg if not w["in_active_cron"]]
        self.assertIn("05-supervisor-stall-scan.py", orphans)
        self.assertIn("queue_watch.py", orphans)
        self.assertIn("supervisor-stall-scan", dups)
        self.assertEqual(
            sorted(dups["supervisor-stall-scan"]),
            ["05-supervisor-stall-scan.py", "supervisor-stall-scan.py"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
