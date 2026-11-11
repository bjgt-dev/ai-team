#!/usr/bin/env python3
"""Sandbox tests for butler_decide.py. No production writes: all outputs go
to /tmp. Run: python3 butler24-test-20261102.py"""
import json
import os
import subprocess
import sys
import tempfile

SCRIPT = "/home/ubuntu/ai-team/scripts/butler_decide.py"
FAILURES = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (" " + str(extra) if extra else ""))
    if not cond:
        FAILURES.append(name)


def run_task(task, extra_env=None, needs=False):
    d = tempfile.mkdtemp()
    tj = os.path.join(d, "task.json")
    lj = os.path.join(d, "ledger.jsonl")
    aj = os.path.join(d, "alerts.jsonl")
    json.dump(task, open(tj, "w"))
    env = dict(os.environ)
    env["BUTLER_MODEL_MOCK"] = "ok"
    if extra_env:
        env.update(extra_env)
    cmd = ["python3", SCRIPT, tj, lj, aj] + (["--needs-decision"] if needs else [])
    p = subprocess.run(cmd, capture_output=True, text=True, env=env)
    rec = json.loads(p.stdout) if p.returncode == 0 else {}
    return p, rec, lj, aj


def main():
    sys.path.insert(0, "/home/ubuntu/ai-team/scripts")
    import butler_decide as bd

    # Rules branches
    v, _, _, _ = bd.decide_rules({"verifier_pass": True, "receipt": {"token": "t", "sha": "a" * 64, "bytes": 10}})
    check("rules DONE", v == "DONE")
    v, _, _, _ = bd.decide_rules({"verifier_pass": False, "receipt": {"token": "", "sha": "", "bytes": 0}, "retry_count": 0, "max_attempts": 3})
    check("rules FAILED", v == "FAILED")
    v, _, _, _ = bd.decide_rules({"verifier_pass": False, "receipt": {}, "retry_count": 3, "max_attempts": 3})
    check("rules DEAD", v == "DEAD")
    v, _, _, _ = bd.decide_rules({"timeout_expired": True})
    check("rules NO_CHANGE on timeout", v == "NO_CHANGE")
    check("priority P0<biz<infra",
          bd.priority_key("P0") < bd.priority_key("biz") < bd.priority_key("infra"))
    # Schema
    ok, _ = bd.validate_model_output({"verdict": "DECIDE", "level": "L2", "conditions": [], "rationale": "r"})
    check("schema good", ok)
    ok, _ = bd.validate_model_output({"verdict": "DECIDE", "level": "L3", "conditions": [], "rationale": "r"})
    check("schema rejects L3", not ok)
    ok, _ = bd.validate_model_output({"verdict": "MAYBE", "level": "L1", "conditions": [], "rationale": "r"})
    check("schema rejects bad verdict", not ok)
    # Context pack truncation + sha
    pack, sha, trunc = bd.build_context_pack({"task_id": "x", "pad": "y" * 20000})
    check("pack truncated + sha64", trunc and len(sha) == 64)
    # Switch OFF: model path refused (needs_decision task with no rule match).
    # Independent of the live switch value: monkeypatch read_switch to "0".
    # (The live switch is 1 since the 2026-11-02 flip; the old subprocess
    # form assumed the pre-flip value and broke after the flip.)
    orig_sw = bd.read_switch
    bd.read_switch = lambda: "0"
    try:
        d = tempfile.mkdtemp()
        lj = os.path.join(d, "ledger.jsonl")
        aj = os.path.join(d, "alerts.jsonl")
        rec = bd.run({"task_id": "t-need", "needs_decision": True,
                      "receipt": {"token": "t", "sha": "b" * 64, "bytes": 5}}, lj, aj)
        check("switch OFF falls back to rules", rec.get("path") == "rules_only_fallback")
    finally:
        bd.read_switch = orig_sw
    # Failure chain: mock fail -> probe gate fails -> NO_CHANGE + alert
    # (switch must be 1; monkeypatch). Model chain itself is exercised only
    # when probe passes; probe-failure alerting is the fail-closed behavior.
    orig = bd.read_switch
    bd.read_switch = lambda: "1"
    try:
        d = tempfile.mkdtemp()
        lj = os.path.join(d, "ledger.jsonl")
        aj = os.path.join(d, "alerts.jsonl")
        os.environ["BUTLER_MODEL_MOCK"] = "fail"
        rec = bd.run({"task_id": "t-fail", "needs_decision": True,
                      "receipt": {"token": "t", "sha": "c" * 64, "bytes": 5}}, lj, aj)
        check("failure ends NO_CHANGE", rec.get("verdict") == "NO_CHANGE")
        check("failure writes alert", os.path.exists(aj) and os.path.getsize(aj) > 0)
    finally:
        bd.read_switch = orig
        os.environ.pop("BUTLER_MODEL_MOCK", None)
    print("FAILURES:", FAILURES if FAILURES else "none")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
