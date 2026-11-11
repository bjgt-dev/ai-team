#!/usr/bin/env python3
"""verdict-conditions-check-20261107.py — cq-scan L5.

Machine-check that a butler verdict's conditions actually landed in the
contract that will be dispatched for it. Before this script, conditions
were only pasted as free text into the contract input by
verdict-auto-dispatch.py and nothing ever verified they survived: a
future edit that drops, truncates or paraphrases a condition would
silently dispatch a contract the butler never approved.

Rules (deterministic, stdlib only, no network, no DB):
  1. verdict must be "通过" or "有条件通过".
  2. "有条件通过" must carry a non-empty conditions list of non-empty
     strings (a conditional pass with zero conditions is a contradiction).
  3. Every condition must appear verbatim (whitespace-insensitive,
     first 80 chars as anchor) in the contract text (input + pass_line
     + evidence + forbidden + five_part values). A missing anchor means
     that condition was dropped in the build step.
  4. The contract must still pass taskcontract_gate (8 fields, five_part,
     variants shape, code workdir) -- the gate is the insert guard, this
     check is the conditions guard; both must pass before dispatch.

Usage:
  python3 verdict-conditions-check-20261107.py --verdict <verdict.json> --contract <contract.json>
Exit 0 = CHECK PASS, 1 = CHECK FAIL (prints each missing condition).
"""
import argparse
import json
import re
import sys

PASS_VERDICTS = ("通过", "有条件通过")
ANCHOR_LEN = 80


def _norm(s):
    """Collapse all whitespace so line-wrap/paste differences do not
    break matching. Chinese text has no word spaces; the anchor match
    is therefore exact on content."""
    return re.sub(r"\s+", "", str(s or ""))


def _contract_text(contract):
    parts = []
    for key in ("input", "pass_line", "evidence"):
        parts.append(contract.get(key) or "")
    forb = contract.get("forbidden") or []
    if isinstance(forb, list):
        parts.extend(str(x) for x in forb)
    fp = contract.get("five_part") or {}
    if isinstance(fp, dict):
        parts.extend(str(v) for v in fp.values())
    return _norm("\n".join(parts))


def check(verdict, contract):
    """Return a list of error strings; empty list means CHECK PASS."""
    errors = []
    if not isinstance(verdict, dict):
        return ["verdict is not a JSON object"]
    if not isinstance(contract, dict):
        return ["contract is not a JSON object"]
    v = verdict.get("verdict", "")
    if v not in PASS_VERDICTS:
        return ["verdict is not pass/conditional pass: %r" % v]
    conds = verdict.get("conditions") or []
    if v == "有条件通过":
        if not isinstance(conds, list) or not conds:
            errors.append("conditional pass with no conditions: nothing to enforce")
        elif any(not isinstance(c, str) or not c.strip() for c in conds):
            errors.append("conditions must be a list of non-empty strings")
    text = _contract_text(contract)
    for i, cond in enumerate(conds):
        if not isinstance(cond, str) or not cond.strip():
            continue
        anchor = _norm(cond)[:ANCHOR_LEN]
        if len(anchor) < 10:
            errors.append("conditions[%d] too short to anchor: %r" % (i, cond[:40]))
        elif anchor not in text:
            errors.append("condition[%d] not in contract: %s..." % (i, cond.strip()[:60]))
    return errors


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check verdict conditions landed in contract")
    ap.add_argument("--verdict", required=True)
    ap.add_argument("--contract", required=True)
    a = ap.parse_args(argv)
    try:
        verdict = json.load(open(a.verdict, encoding="utf-8"))
    except Exception as exc:
        print("CHECK FAIL: cannot read verdict %s: %s" % (a.verdict, exc))
        return 1
    try:
        contract = json.load(open(a.contract, encoding="utf-8"))
    except Exception as exc:
        print("CHECK FAIL: cannot read contract %s: %s" % (a.contract, exc))
        return 1
    errors = check(verdict, contract)
    if errors:
        print("CHECK FAIL: %s" % "; ".join(errors))
        return 1
    n = len(verdict.get("conditions") or [])
    print("CHECK PASS: verdict=%s conditions=%d all present in contract" % (verdict.get("verdict"), n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
