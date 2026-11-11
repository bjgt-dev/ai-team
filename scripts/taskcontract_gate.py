#!/usr/bin/env python3
"""taskcontract_gate.py — deterministic TaskContract gate (l3-20261105-2a).

Validates the forced 8-field TaskContract before any central_tasks insert.
OpenClaw (dispatch) MUST call this gate before inserting into production
central_tasks. A refusal returns the task to pending, never dispatch.

Usage:
  python3 scripts/taskcontract_gate.py --validate <contract.json>
      exit 0 = VALID, exit 1 = INVALID (prints the reason / missing fields)
  python3 scripts/taskcontract_gate.py --insert-guard <contract.json>
      same validation; prints INSERT ALLOWED / INSERT REFUSED.
      INSERT REFUSED means: do NOT insert into central_tasks; task stays pending.

Rules (from state/contract-mandatory-20261105/CONTRACT-TEMPLATE.md):
  Required fields (all 8, non-empty): task_type, input, output_path,
      evidence, pass_line, fail_action, verifier, forbidden.
  task_type whitelist: llm_doc, script, patrol, timer, code, eval.
  eval tasks (Eval OS phase 2, G3 2026-11-11): 8 required fields only;
  NOT contract-bearing (no five_part), no workdir / bundle /
  test_command (those are code/script-only). Optional extras eval_id /
  eval_run_id are ignored here; gate-svc gate 2 checks them at dispatch (G4).
  code tasks additionally require a non-empty workdir (top-level "workdir"
      field, or "workdir" inside "input" when input is an object).
  Thorsten Ball Rule 3 (2026-11-07): contract-bearing tasks (llm_doc,
      script, code) additionally require a non-empty "five_part" object
      with all five keys non-empty: goal, constraint, stop_when, must_not,
      report. Missing any one section -> INSERT REFUSED.
  Thorsten Ball Rule 8 (2026-11-07): optional "variants" field, when
      present, must be a list; each entry must be an object with a
      non-empty "name". Fuzzy tasks MUST fill variants (N variants +
      comparison); well-defined tasks may pass an empty list. Enforced
      (SOP-scan fix 2026-11-07): a task declaring fuzzy (top-level
      "fuzzy": true, or input.ambiguous: true) with missing/empty
      variants -> INSERT REFUSED.
  Thorsten Ball Rules 3/4 support (2026-11-07, strict since cutover51
  2026-11-10): "test_command" is MANDATORY for code/script (absence is a
  refusal, no longer na); "test_baseline_seconds" mandatory for
  code/script; "estimated_seconds" mandatory for code; >1200s needs
  checkpoints. "na" is banned in every field; exemptions only via
  /approve waived record. five_part sections have minimum lengths
  (goal>=10, others>=5); forbidden/allowed_dependencies must parse
  into lists.
  Malformed JSON is invalid (exit 1).
  Extra fields are ignored. No LLM judgement, no network, stdlib only.

NOTE: scripts/contract_validator.py is a tool-allowlist validator, NOT this
8-field contract validator. Do not substitute one for the other.
"""
import argparse
import json
import os
import re
import sys

REQUIRED_FIELDS = [
    "task_type",
    "input",
    "output_path",
    "evidence",
    "pass_line",
    "fail_action",
    "verifier",
    "forbidden",
]

TASK_TYPE_WHITELIST = {"llm_doc", "script", "patrol", "timer", "code", "eval"}  # G3: +eval (Eval OS phase 2, 2026-11-11)

# Thorsten Ball Rule 3: five-part contract sections (required for
# contract-bearing task types: llm_doc, script, code).
FIVE_PART_KEYS = ["goal", "constraint", "stop_when", "must_not", "report"]
CONTRACT_BEARING_TYPES = {"llm_doc", "script", "code"}


def _is_empty(value):
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _get_workdir(contract):
    workdir = contract.get("workdir")
    if not _is_empty(workdir):
        return workdir
    inp = contract.get("input")
    if isinstance(inp, dict):
        workdir = inp.get("workdir")
        if not _is_empty(workdir):
            return workdir
    return None


def _min_len(value, n):
    return isinstance(value, str) and len(value.strip()) >= n


def _is_na(value):
    return isinstance(value, str) and value.strip().lower() == "na"


def _parse_str_list(value):
    """Parse a value into a list of non-empty strings; None if unparseable."""
    if isinstance(value, list):
        if any(not isinstance(x, str) or not x.strip() for x in value):
            return None
        return [x.strip() for x in value]
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace("\n", ",").split(",")]
        parts = [p for p in parts if p]
        return parts if parts else None
    return None


def validate(contract):
    """Return a list of error strings; empty list means VALID.
    Cutover51 strict content validation (W5.2): shape checks replaced by
    content checks. 'na' is banned everywhere; test_command is mandatory
    for code/script; goal/deliverable have minimum lengths; forbidden and
    allowed_dependencies must parse into lists. Strict is the default and
    only mode; there is no lenient flag."""
    errors = []
    if not isinstance(contract, dict):
        return ["contract is not a JSON object"]
    for field in REQUIRED_FIELDS:
        if field not in contract or _is_empty(contract[field]):
            errors.append("missing field: %s" % field)
        elif _is_na(contract[field]):
            errors.append("field %s must not be 'na' (cutover51: na banned, use /approve waived record for exemptions)" % field)
    task_type = contract.get("task_type")
    if not _is_empty(task_type) and task_type not in TASK_TYPE_WHITELIST:
        errors.append(
            "invalid task_type: %r (allowed: %s)"
            % (task_type, ", ".join(sorted(TASK_TYPE_WHITELIST)))
        )
    if task_type == "code":
        if _is_empty(_get_workdir(contract)):
            errors.append("missing field: workdir (required for code tasks)")
    # Butler C5 (verdict verifier-gates-234-20261106, 2026-11-08): new code
    # contracts must require PULLONCE-BUNDLE-V1, so a legacy single .py
    # delivery can no longer bypass gates 3/4. Already-dispatched tasks are
    # not re-validated; this only gates NEW dispatches (legacy cutoff
    # 2026-11-08: contracts validated from this date on must declare it).
    if task_type == "code":
        if contract.get("format") != "PULLONCE-BUNDLE-V1" and contract.get("require_bundle") is not True:
            errors.append("code tasks must declare format PULLONCE-BUNDLE-V1 (or require_bundle true); legacy single-.py no longer accepted for new contracts (butler verifier-gates-234 cond 5)")
    # Thorsten Ball Rule 3: five-part contract (Goal / Constraint /
    # Stop when / Must not / Report). Missing any section -> refuse.
    # Cutover51 content check: minimum lengths, single-letter goal rejected.
    if task_type in CONTRACT_BEARING_TYPES:
        fp = contract.get("five_part")
        if not isinstance(fp, dict):
            errors.append("missing field: five_part (Goal/Constraint/Stop when/Must not/Report, required for %s)" % task_type)
        else:
            _min = {"goal": 10, "constraint": 5, "stop_when": 5, "must_not": 5, "report": 5}
            for key in FIVE_PART_KEYS:
                v = fp.get(key)
                if _is_empty(v):
                    errors.append("missing five_part section: %s" % key)
                elif _is_na(v):
                    errors.append("five_part.%s must not be 'na'" % key)
                elif not _min_len(v, _min[key]):
                    errors.append("five_part.%s too short: %d chars < %d (content check, cutover51)" % (key, len(str(v).strip()), _min[key]))
    # deliverable (output_path) minimum length / determinability.
    if not _is_empty(contract.get("output_path")) and not _min_len(contract.get("output_path"), 5):
        errors.append("output_path too short: %r < 5 chars (content check, cutover51)" % contract.get("output_path"))
    # Rule 14 forbidden: must parse into a list of >=1 entries, each >=2 chars.
    if "forbidden" in contract and not _is_empty(contract.get("forbidden")):
        _fb = _parse_str_list(contract.get("forbidden"))
        if _fb is None or len(_fb) == 0:
            errors.append("invalid forbidden: must parse into a non-empty list of paths/patterns")
        elif any(len(x) < 2 for x in _fb):
            errors.append("invalid forbidden: each entry must be >= 2 chars (content check, cutover51)")
    # allowed_dependencies: when present must parse into a list; required for code.
    if task_type == "code" and "allowed_dependencies" not in contract:
        errors.append("missing field: allowed_dependencies (required for code tasks; use [] when none)")
    if "allowed_dependencies" in contract and contract.get("allowed_dependencies") is not None:
        if _parse_str_list(contract.get("allowed_dependencies")) is None:
            errors.append("invalid allowed_dependencies: must parse into a list")
    # Thorsten Ball Rules 3/4 (cutover51 strict): test_command is MANDATORY
    # for code/script — no more na. test_baseline_seconds is mandatory for
    # code/script and bounds the reasonableness interval (0 < s <= 3600).
    if task_type in ("code", "script"):
        if "test_command" not in contract or _is_empty(contract.get("test_command")):
            errors.append("missing field: test_command (mandatory for %s; na no longer accepted, cutover51)" % task_type)
        if "test_baseline_seconds" not in contract or contract.get("test_baseline_seconds") is None:
            errors.append("missing field: test_baseline_seconds (mandatory for %s; reasonableness interval, cutover51)" % task_type)
        elif not isinstance(contract.get("test_baseline_seconds"), (int, float)) or not (0 < contract["test_baseline_seconds"] <= 3600):
            errors.append("invalid test_baseline_seconds: must be a number in (0, 3600]")
    # Plan A (g5-planid-dispatch-fix-20261111): plan_id is MANDATORY for
    # code/script — the S4 lease trigger requires a real plan_approvals UUID
    # at claim time. Only presence + UUID format are enforced here (fail
    # fast); existence is verified at dispatch (dispatch_task.py) and at
    # gate-svc /gate/dispatch (plan-id-exists gate), both existence-only
    # (never the 24h TTL / slug rule, or Cases would be refused after
    # 2026-11-12 06:48).
    if task_type in ("code", "script"):
        _pid = contract.get("plan_id")
        if _is_empty(_pid):
            errors.append("missing field: plan_id (mandatory for %s; must be a plan_approvals UUID, g5-planid-dispatch-fix-20261111)" % task_type)
        elif not isinstance(_pid, str) or not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", _pid.strip()):
            errors.append("invalid plan_id: must be a UUID (plan_approvals row)")
    if "test_command" in contract and contract.get("test_command") is not None:
        tc = contract.get("test_command")
        if isinstance(tc, str):
            if not tc.strip():
                errors.append("invalid test_command: must be a non-empty string or argv list")
        elif isinstance(tc, list):
            if not tc or any(not isinstance(x, str) or not x.strip() for x in tc):
                errors.append("invalid test_command: argv list must be non-empty strings")
        else:
            errors.append("invalid test_command: must be a string or argv list")
    if "test_files" in contract and contract.get("test_files") is not None:
        tf = contract.get("test_files")
        if not isinstance(tf, list) or any(not isinstance(x, str) or not x.strip() for x in tf):
            errors.append("invalid test_files: must be a list of non-empty paths")
    # W3.4 duration fields (code only): estimated_seconds mandatory; if the
    # estimate exceeds 20 min (1200 s) the contract must declare checkpoints.
    if task_type == "code":
        if "estimated_seconds" not in contract or contract.get("estimated_seconds") is None:
            errors.append("missing field: estimated_seconds (mandatory for code tasks, cutover51 W3.4)")
        elif not isinstance(contract.get("estimated_seconds"), (int, float)) or contract["estimated_seconds"] <= 0:
            errors.append("invalid estimated_seconds: must be a number > 0")
        elif contract["estimated_seconds"] > 1200:
            cps = contract.get("checkpoints")
            if not isinstance(cps, list) or len(cps) == 0:
                errors.append("estimated_seconds %s > 1200 without checkpoints: code contract over 20 min must declare segmented checkpoints (W3.4)" % contract["estimated_seconds"])
    # Thorsten Ball Rule 8: variants shape check (only when present).
    # SOP-scan fix 2026-11-07: the docstring always said "Fuzzy tasks MUST
    # fill variants", but the code only shape-checked a present list -- a
    # fuzzy task with variants missing/[] sailed through. Now enforced:
    # contract declares fuzzy (top-level "fuzzy": true, or
    # input.ambiguous: true) -> variants must be a non-empty list.
    _inp = contract.get("input")
    _fuzzy = contract.get("fuzzy") is True or (isinstance(_inp, dict) and _inp.get("ambiguous") is True)
    if "variants" in contract and contract.get("variants") is not None:
        vs = contract.get("variants")
        if not isinstance(vs, list):
            errors.append("invalid variants: must be a list of {name, ...}")
        else:
            for i, v in enumerate(vs):
                if not isinstance(v, dict) or _is_empty(v.get("name")):
                    errors.append("invalid variants[%d]: each variant needs a non-empty name" % i)
            if _fuzzy and len(vs) == 0:
                errors.append("fuzzy task must fill variants (N variants + comparison table); empty list refused (Rule 8)")
    elif _fuzzy:
        errors.append("fuzzy task must fill variants (N variants + comparison table); variants missing (Rule 8)")
    # (test_command / test_files shape checks live in the cutover51 strict
    # block above; absence is no longer na for code/script.)
    # Observe-correct loop (2026-11-09): a task signature that failed
    # twice and is quarantined is blocked from re-dispatch until the
    # reflection is resolved and the signature is removed from the blocklist.
    _tid = contract.get("task_id")
    if isinstance(_tid, str) and _tid:
        _sig = re.sub(r"-[0-9a-f]{8}-[0-9a-f-]+$", "", _tid.strip())
        _sig = re.sub(r"-[0-9]+$", "", _sig)
        _sig = re.sub(r":.*$", "", _sig)
        _bl = "/home/ubuntu/ai-team/state/observe-correct-20261109/blocked-signatures.json"
        try:
            if os.path.exists(_bl):
                _blocked = json.load(open(_bl))
                if _sig in _blocked:
                    _ref = _blocked[_sig].get("reflection", "")
                    errors.append(
                        "observe-correct block: signature %r quarantined %d times; "
                        "fix the root cause first, see %s" % (
                            _sig, _blocked[_sig].get("count", 0), _ref))
        except Exception:
            pass  # blocklist unreadable -> do not block dispatch on a read error
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description="TaskContract gate (l3-20261105-2a)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--validate", metavar="CONTRACT_JSON",
                       help="validate a contract JSON file")
    group.add_argument("--insert-guard", metavar="CONTRACT_JSON",
                       help="insert guard: same validation, INSERT ALLOWED/REFUSED")
    args = parser.parse_args(argv)
    path = args.validate or args.insert_guard
    mode = "insert-guard" if args.insert_guard else "validate"
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError as exc:
        print("INVALID: cannot read %s: %s" % (path, exc))
        return 1
    try:
        contract = json.loads(raw)
    except json.JSONDecodeError as exc:
        print("INVALID: malformed JSON in %s: %s" % (path, exc))
        return 1
    errors = validate(contract)
    if errors:
        if mode == "insert-guard":
            print("INSERT REFUSED: %s" % "; ".join(errors))
        else:
            print("INVALID: %s" % "; ".join(errors))
        return 1
    if mode == "insert-guard":
        print("INSERT ALLOWED: contract valid")
    else:
        print("VALID")
    return 0


if __name__ == "__main__":
    sys.exit(main())
