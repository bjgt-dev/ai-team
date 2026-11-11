#!/usr/bin/env python3
"""Regression tests for the 2026-11-07 enforce scan (all gaps patched
this round). Run on the hub:
  python3 scripts/enforce-scan-20261107-tests.py
Exits 0 only when every check passes; prints one PASS/FAIL line per
check plus a summary. Uses only /tmp fixtures (temp queue, temp
outbox, temp source files); it NEVER writes production state, NEVER
touches central_tasks, never sends anything. The only production reads
are the four-category gate file (imported) and the scripts under test.

Covers:
  G1 verifier five gates (fake-1/2/3 regression samples must reject)
  G2 taskcontract_gate fuzzy variants + test_command shape
  G3 verdict-dispatch-watchdog normalization + title refresh rule
  G4 verdict-auto-dispatch task_id sanitization
  G5 health_3q_supplement gate-aware pending count
  G6 notify.py push gating + close of auto_decisions entries
  G7 watchdog-20261106 gate-tagged queue entries + dedup
  G8 watchdog registry checker logic (synthetic fixture)
"""
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path

BASE = "/home/ubuntu/ai-team"
SCRIPTS = os.path.join(BASE, "scripts")
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print("%s %s %s" % ("PASS" if cond else "FAIL", name,
                        ("| " + str(detail)[:160]) if detail else ""))


def load(path, modname):
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    tmp = tempfile.mkdtemp(prefix="enforce-scan-20261107-")

    # ---- G1: verifier five gates ----
    v = load(os.path.join(SCRIPTS, "c2-verifier-20261104.py"), "c2v")
    # fake-1: 156B pytest stub, no source -> gate 5 rejects
    fake1 = b"pytest_exit=0\n1 passed in 0.00s\n"
    check("G1 fake-1 pytest stub rejected (gate5)",
          v._gate5_code([], fake1)[0] == "reject", v._gate5_code([], fake1))
    check("G1 fake-1 with a source present passes gate5",
          v._gate5_code(["/tmp/x.py"], fake1)[0] == "pass")
    # fake-2: llm_doc echo -> gate 5 rejects; different text passes
    check("G1 fake-2 llm_doc echo rejected (gate5)",
          v._gate5_llm_echo({"content": "hello world"}, b"hello world")[0] == "reject")
    check("G1 llm_doc different text passes gate5",
          v._gate5_llm_echo({"content": "hello world"}, b"hello brave world")[0] == "pass")
    # fake-3: pass-only skeleton -> gate 2 rejects; real code passes
    skel = os.path.join(tmp, "audit.py")
    open(skel, "w").write("def audit():\n    pass\n\ndef main():\n    pass\n")
    good = os.path.join(tmp, "good.py")
    open(good, "w").write("def audit():\n    return 1\n")
    check("G1 fake-3 skeleton rejected (gate2)",
          v._ast_check_py(skel)[0] == "reject", v._ast_check_py(skel))
    check("G1 real code passes gate2",
          v._ast_check_py(good)[0] == "pass")
    # gate 1: missing output rejected, existing passes, URL is na
    check("G1 gate1 missing output rejected",
          v._gate1_output_paths({"output_path": "/tmp/nope-xyz-1107.md"})[0] == "reject")
    check("G1 gate1 existing output passes",
          v._gate1_output_paths({"output_path": good})[0] == "pass")
    check("G1 gate1 URL is na (not a local path)",
          v._gate1_output_paths({"output_path": "https://example.com/doc"})[0] == "na")
    # gates 3/4: na without test_command; pass/fail with a declared command
    check("G1 gate3 na without test_command",
          v._gate3_rerun({}, [good])[0] == "na")
    check("G1 gate4 na without test_command",
          v._gate4_redgreen({}, [good])[0] == "na")
    t = os.path.join(tmp, "check_good.py")
    open(t, "w").write("from good import audit\nassert audit() == 1\n")
    vac = os.path.join(tmp, "check_vac.py")
    open(vac, "w").write("assert True\n")
    c = {"test_command": ["python3", "check_good.py"], "test_files": [t]}
    check("G1 gate3 re-run passes on good source",
          v._gate3_rerun(c, [good])[0] == "pass", v._gate3_rerun(c, [good]))
    check("G1 gate4 red-green passes on good source",
          v._gate4_redgreen(c, [good])[0] == "pass")
    c2 = {"test_command": ["python3", "check_vac.py"], "test_files": [vac]}
    check("G1 gate4 vacuous test rejected (passes on stub)",
          v._gate4_redgreen(c2, [good])[0] == "reject", v._gate4_redgreen(c2, [good]))
    # note: test file imports `good`, so the failing case uses a good.py
    # whose audit() returns the wrong value.
    badgood = os.path.join(tmp, "good2.py")
    open(badgood, "w").write("def audit():\n    return 2\n")
    t2 = os.path.join(tmp, "check_good2.py")
    open(t2, "w").write("from good2 import audit\nassert audit() == 1\n")
    c3 = {"test_command": ["python3", "check_good2.py"], "test_files": [t2]}
    check("G1 gate3 re-run rejects failing source",
          v._gate3_rerun(c3, [badgood])[0] == "reject")

    # ---- G2: taskcontract_gate ----
    g = load(os.path.join(SCRIPTS, "taskcontract_gate.py"), "tcg")
    base = {"task_type": "code", "input": "x", "output_path": "/tmp/o.md",
            "evidence": "e", "pass_line": "p", "fail_action": "f",
            "verifier": "v", "forbidden": "n", "workdir": "/tmp/wd",
            "five_part": {"goal": "g", "constraint": "c", "stop_when": "s",
                          "must_not": "m", "report": "r"}}
    check("G2 valid contract passes",
          g.validate(dict(base)) == [], g.validate(dict(base)))
    check("G2 fuzzy + empty variants refused",
          any("fuzzy" in e for e in g.validate(dict(base, fuzzy=True, variants=[]))))
    check("G2 input.ambiguous + missing variants refused",
          any("fuzzy" in e for e in g.validate(
              dict(base, input={"ambiguous": True}))))
    check("G2 fuzzy + variants passes",
          g.validate(dict(base, fuzzy=True, variants=[{"name": "A"}])) == [])
    check("G2 non-fuzzy + empty variants passes",
          g.validate(dict(base, variants=[])) == [])
    check("G2 empty test_command refused",
          any("test_command" in e for e in g.validate(dict(base, test_command=""))))
    check("G2 well-formed test_command passes",
          g.validate(dict(base, test_command=["python3", "check.py"],
                          test_files=["/tmp/check.py"])) == [])
    check("G2 five_part incomplete refused",
          any("five_part" in e for e in g.validate(dict(base, five_part={"goal": "g"}))))

    # ---- G3: verdict-dispatch-watchdog ----
    w = load(os.path.join(SCRIPTS, "verdict-dispatch-watchdog.py"), "vdw")
    titles = {"kids-iteration4-p0-20261107", "verifier-gates-234-p0-20261106"}
    check("G3 iter4 matches iteration4 title",
          w.is_dispatched("kids-iter4-20261107", titles) is True)
    check("G3 iteration4 matches iter4 title",
          w.is_dispatched("kids-iteration4-20261107",
                          {"kids-iter4-20261107-p0-20261107"}) is True)
    check("G3 unknown verdict does not match",
          w.is_dispatched("something-new-20261107", titles) is False)
    check("G3 spaced task matches sanitized title",
          w.is_dispatched("kids iter 4", {"kids-iter-4-p0-20261107"}) is True)
    check("G3 normalize strips -p0",
          w.normalize_task("kids-iteration4-p0-20261107") == "kids-iter4-20261107")

    # ---- G4: verdict-auto-dispatch ----
    ad = load(os.path.join(SCRIPTS, "verdict-auto-dispatch.py"), "vad")
    tid_re = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    check("G4 spaced task id sanitized + legal",
          tid_re.match(ad.safe_task_id("kids iter 4", "20261107")) is not None,
          ad.safe_task_id("kids iter 4", "20261107"))
    check("G4 live id unchanged",
          ad.safe_task_id("kids-iteration4-20261107", "20261107") == "kids-iteration4-20261107-p0-20261107")
    check("G4 slash task id sanitized",
          tid_re.match(ad.safe_task_id("a/b c", "")) is not None,
          ad.safe_task_id("a/b c", ""))

    # ---- G5: health_3q supplement ----
    s = load(os.path.join(SCRIPTS, "health_3q_supplement-20261104.py"), "supp")
    pend = [{"needs_chairman": True}, {"needs_chairman": False}, {},
            {"needs_chairman": False}]
    check("G5 chairman count excludes auto items (expect 2)",
          s.chairman_pending_count(pend) == 2, s.chairman_pending_count(pend))
    check("G5 all-auto pending counts 0",
          s.chairman_pending_count([{"needs_chairman": False}]) == 0)

    # ---- G6: notify.py push gating + close ----
    n = load(os.path.join(BASE, "ceo-seat", "work", "notify.py"), "notify")
    nq = os.path.join(tmp, "decision-queue.json")
    json.dump({"pending": [], "resolved": [], "auto_decisions": []},
              open(nq, "w"))
    n.QUEUE_PATH = nq

    class A:
        pass
    a = A()
    a.type = "T"
    a.task_id = "auto-1"
    a.summary = "routine technical cleanup, reversible"
    a.five_step = ""
    a.options = ""
    a.deadline_hint = ""
    n.cmd_push(a)
    q = json.load(open(nq))
    check("G6 technical item goes to auto_decisions, not pending",
          len(q["auto_decisions"]) == 1 and len(q["pending"]) == 0, q)
    check("G6 auto entry tagged needs_chairman=false",
          q["auto_decisions"][0].get("needs_chairman") is False)
    a2 = A()
    a2.type = "T2"
    a2.task_id = "pay-1"
    a2.summary = "need to buy a subscription, costs money"
    a2.five_step = ""
    a2.options = ""
    a2.deadline_hint = ""
    n.cmd_push(a2)
    q = json.load(open(nq))
    check("G6 money item goes to pending tagged chairman",
          len(q["pending"]) == 1 and q["pending"][0].get("needs_chairman") is True,
          q["pending"][0].get("category"))
    eid = q["auto_decisions"][0]["id"]
    a3 = A()
    a3.id = eid
    a3.reason = "test close"
    n.cmd_close(a3)
    q = json.load(open(nq))
    check("G6 auto_decisions entry can be closed to resolved",
          len(q["auto_decisions"]) == 0 and len(q["resolved"]) == 1 and
          q["resolved"][0].get("close_reason") == "test close", q["resolved"])

    # ---- G7: watchdog-20261106 gate-tagged filing ----
    wd = load(os.path.join(SCRIPTS, "watchdog-20261106.py"), "wdog")
    wq = Path(tmp) / "wd-queue.json"
    wq.write_text(json.dumps({"pending": [], "resolved": [], "auto_decisions": []}))
    wd.DECISION_QUEUE = wq
    wd.OUTBOX = Path(tmp) / "outbox"
    findings = {"stale": [{"task_id": "abc12345", "task_type": "code",
                           "status": "CLAIMED", "sla_minutes": 60,
                           "stuck_minutes": 75.5,
                           "last_change": "2026-11-07T09:00:00+00:00",
                           "owner": "worker-1"}],
                "failure_rate": {"fired": False},
                "done_stall": {"fired": True, "ready_plus_retrywait": 3}}
    filed, deduped = wd.file_live(findings)
    q = json.loads(wq.read_text())
    check("G7 watchdog files 2 triggers",
          len(filed) == 2, filed)
    check("G7 watchdog entries are full-schema + gate-tagged",
          all(set(("id", "type", "task_id", "summary", "options",
                   "needs_chairman", "category", "dedup_key")) <= set(e)
              for e in q["pending"]),
          [(e["id"], e.get("needs_chairman")) for e in q["pending"]])
    check("G7 watchdog stale entry tagged auto (technical)",
          q["pending"][0].get("needs_chairman") is False,
          q["pending"][0].get("category"))
    filed2, deduped2 = wd.file_live(findings)
    check("G7 second run dedupes (no re-file)",
          filed2 == [] and len(deduped2) == 2, deduped2)
    check("G7 stale threshold logic",
          wd.check_stale([{"task_id": "t1", "task_type": "code",
                           "status": "CLAIMED",
                           "updated_at": __import__("datetime").datetime.now(
                               __import__("datetime").timezone.utc) -
                           __import__("datetime").timedelta(minutes=61),
                           "owner": ""}],
                          __import__("datetime").datetime.now(
                              __import__("datetime").timezone.utc)) != [])
    check("G7 failure-rate threshold (3/6 fires, 1/6 does not)",
          wd.check_failure_rate(3, 6)["fired"] is True and
          wd.check_failure_rate(1, 6)["fired"] is False)
    check("G7 done-stall fires on 0 DONE + pending",
          wd.check_done_stall(0, 3)["fired"] is True and
          wd.check_done_stall(2, 3)["fired"] is False)

    # ---- G8: registry checker logic (synthetic fixture) ----
    rc = load(os.path.join(SCRIPTS, "watchdog-registry-check-20261107.py"), "regcheck")
    fx = Path(tmp) / "fx"
    (fx / "scripts").mkdir(parents=True)
    for f in ("alert_shadow_watch.py", "loop-watchdog.py", "brain_watch.sh",
              "storage_scan-20261104.py"):
        (fx / "scripts" / f).write_text("# stub\n")
    (fx / "logs").mkdir()
    (fx / "logs" / "shadow-watch.log").write_text("x")
    os.utime(fx / "logs" / "shadow-watch.log",
             (os.path.getmtime(fx / "logs" / "shadow-watch.log"),
              __import__("time").time() - 60))
    cron = rc._cron_lines(
        "* * * * * python3 /home/ubuntu/ai-team/scripts/alert_shadow_watch.py\n"
        "# DISABLED-20261104-13 */5 * * * * /home/ubuntu/ai-team/scripts/brain_watch.sh\n"
        "30 2 * * * python3 /home/ubuntu/ai-team/scripts/timer_submit-20261104.py storage_scan\n")
    check("G8 registry: active + fresh log ok",
          rc.check_entry({"name": "alert_shadow_watch.py", "expected": "active",
                          "log": "logs/shadow-watch.log", "freshness_seconds": 600},
                         cron, str(fx), "")[0] == "ok")
    check("G8 registry: retired commented-out ok",
          rc.check_entry({"name": "brain_watch.sh", "expected": "retired"},
                         cron, str(fx), "")[0] == "ok")
    check("G8 registry: timer-driven ok",
          rc.check_entry({"name": "storage_scan-20261104.py",
                          "expected": "timer-driven", "timer": "storage_scan"},
                         cron, str(fx), "storage_scan-20261104.py")[0] == "ok")
    check("G8 registry: orphan fails",
          rc.check_entry({"name": "loop-watchdog.py", "expected": "orphan",
                          "log": None, "freshness_seconds": None},
                         cron, str(fx), "")[0] == "fail")
    check("G8 registry: active missing from cron fails",
          rc.check_entry({"name": "loop-watchdog.py", "expected": "active",
                          "log": "logs/loop-watchdog.log", "freshness_seconds": 900},
                         cron, str(fx), "")[0] == "fail")

    n_fail = sum(1 for _n, ok, _d in RESULTS if not ok)
    print("SUMMARY: %d/%d passed, %d failed" % (len(RESULTS) - n_fail, len(RESULTS), n_fail))
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
