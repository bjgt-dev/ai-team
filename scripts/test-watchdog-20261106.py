#!/usr/bin/env python3
"""Tests for watchdog-20261106.py pure logic (no DB, no writes).
Positive + negative cases for all three draft §2 triggers."""
import datetime
import importlib.util
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Hub filenames use hyphens (not importable as modules), so load by path.
_wd = _load("watchdog_20261106", "watchdog-20261106.py")
check_done_stall = _wd.check_done_stall
check_failure_rate = _wd.check_failure_rate
check_stale = _wd.check_stale
sla_minutes = _wd.sla_minutes

UTC = datetime.timezone.utc


def row(task_id, task_type, status, minutes_ago, owner="twt"):
    return {"task_id": task_id, "task_type": task_type, "status": status,
            "updated_at": datetime.datetime.now(UTC)
            - datetime.timedelta(minutes=minutes_ago),
            "owner": owner}


class TestSla(unittest.TestCase):
    def test_code_60(self):
        self.assertEqual(sla_minutes("code"), 60)

    def test_llm_doc_30(self):
        self.assertEqual(sla_minutes("llm_doc"), 30)

    def test_patrol_timer_canary_10(self):
        for t in ("patrol", "timer", "canary"):
            self.assertEqual(sla_minutes(t), 10)

    def test_unknown_defaults_60(self):
        self.assertEqual(sla_minutes("script"), 60)
        self.assertEqual(sla_minutes(""), 60)
        self.assertEqual(sla_minutes(None), 60)


class TestStale(unittest.TestCase):
    def test_code_stale_at_61m_positive(self):
        now = datetime.datetime.now(UTC)
        out = check_stale([row("t1", "code", "CLAIMED", 61)], now)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["trigger"], "watchdog-stale")
        self.assertEqual(out[0]["sla_minutes"], 60)

    def test_code_not_stale_at_59m_negative(self):
        now = datetime.datetime.now(UTC)
        self.assertEqual(check_stale([row("t1", "code", "CLAIMED", 59)],
                                     now), [])

    def test_llm_doc_stale_at_31m_positive(self):
        now = datetime.datetime.now(UTC)
        out = check_stale([row("t2", "llm_doc", "RUNNING", 31)], now)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["sla_minutes"], 30)

    def test_patrol_stale_at_11m_positive(self):
        now = datetime.datetime.now(UTC)
        self.assertEqual(len(check_stale(
            [row("t3", "patrol", "VERIFYING", 11)], now)), 1)

    def test_terminal_status_ignored_negative(self):
        now = datetime.datetime.now(UTC)
        self.assertEqual(check_stale(
            [row("t4", "code", "DONE", 9999)], now), [])
        self.assertEqual(check_stale(
            [row("t5", "code", "QUARANTINED", 9999)], now), [])

    def test_retry_wait_counts_as_nonterminal(self):
        now = datetime.datetime.now(UTC)
        out = check_stale([row("t6", "code", "RETRY_WAIT", 61)], now)
        self.assertEqual(len(out), 1)


class TestFailureRate(unittest.TestCase):
    def test_fires_at_50pct_with_4_positive(self):
        r = check_failure_rate(2, 4)
        self.assertTrue(r["fired"])
        self.assertEqual(r["rate"], 0.5)

    def test_fires_above_threshold(self):
        self.assertTrue(check_failure_rate(3, 4)["fired"])

    def test_below_threshold_negative(self):
        self.assertFalse(check_failure_rate(1, 4)["fired"])

    def test_sample_too_small_negative(self):
        # 2/3 = 67% but sample < 4 -> must NOT fire
        self.assertFalse(check_failure_rate(2, 3)["fired"])

    def test_zero_sample_negative(self):
        self.assertFalse(check_failure_rate(0, 0)["fired"])


class TestDoneStall(unittest.TestCase):
    def test_fires_zero_done_with_pending_positive(self):
        r = check_done_stall(0, 3)
        self.assertTrue(r["fired"])

    def test_no_fire_when_done_happening_negative(self):
        self.assertFalse(check_done_stall(1, 3)["fired"])

    def test_no_fire_when_nothing_pending_negative(self):
        self.assertFalse(check_done_stall(0, 0)["fired"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
