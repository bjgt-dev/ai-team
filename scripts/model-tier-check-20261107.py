#!/usr/bin/env python3
"""model-tier-check-20261107.py -- Thorsten Ball Rule 1 模型选型收紧.

Rule 1's SOP landing: "含糊任务、无法自动检查的任务走池子里最强的可用
免费模型 (Tier-1), 不走小模型; 批量机械活小模型即可; 模型选型写进任务
合同的 input, 不留给工人自己猜." Before this script there was no code
at all enforcing or even checking that: code-gen always uses Tier-1, but
no contract ever declared a tier, so the "fuzzy -> Tier-1 / mechanical ->
small" rule was unenforceable and unauditable.

This checker validates one contract JSON:
  * contract declares fuzzy=true (or input.ambiguous=true) -> input.model_tier
    MUST be "tier1"; anything else (or missing) is a FAIL.
  * contract declares mechanical=true -> model_tier may be "small" or
    "tier1" (tier1 on a mechanical task is a WARN: wasteful, not a fail).
  * neither flag -> model_tier, if present, must be one of
    tier1/small; absent is a WARN (selection left to guess, SOP violation
    in spirit but not a hard fail -- the contract did not claim fuzziness).
Exit: 0 = PASS (warnings allowed); 1 = FAIL; 2 = tool error.
Standalone audit gate: wire into dispatch only after a canary run shows
zero false refuses on live contracts (see receipt).
"""
import argparse
import json
import sys

VALID_TIERS = {"tier1", "small"}


def check(contract):
    errors, warnings = [], []
    if not isinstance(contract, dict):
        return ["contract is not a JSON object"], []
    inp = contract.get("input")
    if not isinstance(inp, dict):
        inp = {}
    fuzzy = contract.get("fuzzy") is True or inp.get("ambiguous") is True
    mechanical = contract.get("mechanical") is True
    tier = inp.get("model_tier")
    if fuzzy and mechanical:
        errors.append("contract declares both fuzzy and mechanical")
    if fuzzy:
        if tier != "tier1":
            errors.append("fuzzy task must use model_tier=tier1, got %r" % (tier,))
    elif mechanical:
        if tier is not None and tier not in VALID_TIERS:
            errors.append("mechanical task model_tier must be tier1/small, got %r" % (tier,))
        elif tier == "tier1":
            warnings.append("mechanical task on tier1: wasteful, small suffices")
    else:
        if tier is None:
            warnings.append("no model_tier declared: selection left to guess (SOP: write it in input)")
        elif tier not in VALID_TIERS:
            errors.append("model_tier must be tier1/small, got %r" % (tier,))
    return errors, warnings


def main(argv=None):
    ap = argparse.ArgumentParser(description="Rule 1 model-tier contract checker")
    ap.add_argument("contract", help="contract JSON file")
    args = ap.parse_args(argv)
    try:
        with open(args.contract, encoding="utf-8") as f:
            contract = json.load(f)
    except Exception as exc:
        print("TOOL-ERROR: cannot read %s: %s" % (args.contract, exc))
        return 2
    errors, warnings = check(contract)
    for w in warnings:
        print("WARN: %s" % w)
    if errors:
        print("FAIL %s:" % args.contract)
        for e in errors:
            print("  - %s" % e)
        return 1
    print("PASS %s" % args.contract)
    return 0


if __name__ == "__main__":
    sys.exit(main())
