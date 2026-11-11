#!/usr/bin/env python3
# Contract validator (see contracts/contract.schema.json + the 8-field
# TaskContract template in state/contract-mandatory-20261105/).
# Dual-schema (cq-scan 2026-11-07, L4): the old code only knew the v1.1
# enum {llm_generate, llm_extract_events, shell_exec}, so EVERY current
# contract (task_type llm_doc/script/patrol/timer/code) FAILED here --
# while contracts/CONTRACT_SCHEMA.md still named this file the validator
# and canary_e2e.py / decision_notifier.py still call it. Now:
#   - current 5-type contracts -> delegated to taskcontract_gate.validate
#     (8 fields, five_part, variants shape, code workdir), the same gate
#     dispatch_task.py enforces at insert;
#   - legacy v1.1 contracts (llm_generate etc.) -> the original checks
#     below, unchanged, so the canary_e2e legacy contract still passes.
# Usage: validate_contract.py <contract.json> [...]  -> exit 0 pass, 1 fail.
import json, re, sys

REQUIRED = ["task_id", "task_type", "objective", "output_file", "acceptance_criteria",
            "timeout_s", "max_retries", "idempotency_key", "evidence_required",
            "created_at", "source", "negative_constraints"]
TASK_TYPES = {"llm_generate", "llm_extract_events", "shell_exec"}
NEW_TASK_TYPES = {"llm_doc", "script", "patrol", "timer", "code"}
CRITERION_TYPES = {"file_exists", "file_min_size"}

def check(c):
    """Dispatch by task_type: current 5-type contracts go to the gate,
    legacy v1.1 contracts keep the original checks. Returns error list."""
    if not isinstance(c, dict):
        return ["contract is not a JSON object"]
    tt = c.get("task_type")
    if tt in NEW_TASK_TYPES:
        return _check_current(c)
    if tt is not None and tt not in TASK_TYPES:
        return ["task_type not in enum: %s (current: %s; legacy v1.1: %s)"
                % (tt, ", ".join(sorted(NEW_TASK_TYPES)), ", ".join(sorted(TASK_TYPES)))]
    return _check_legacy(c)

def _check_current(c):
    try:
        import taskcontract_gate as _gate
    except Exception as exc:
        return ["taskcontract_gate unavailable: %s" % exc]
    return list(_gate.validate(c))

def _check_legacy(c):
    errs = []
    for f in REQUIRED:
        if f not in c:
            errs.append("missing required field: %s" % f)
    if errs:
        return errs
    if not re.match(r"^[a-z0-9][a-z0-9\-_]*$", str(c["task_id"])):
        errs.append("task_id pattern violation")
    if c["task_type"] not in TASK_TYPES:
        errs.append("task_type not in enum: %s" % c["task_type"])
    if not isinstance(c["objective"], str) or not c["objective"]:
        errs.append("objective must be non-empty string (input spec)")
    if not isinstance(c["output_file"], str) or not c["output_file"]:
        errs.append("output_file must be non-empty string (output spec)")
    if not (isinstance(c["timeout_s"], int) and 30 <= c["timeout_s"] <= 1800):
        errs.append("timeout_s must be int in [30,1800]")
    if not (isinstance(c["max_retries"], int) and 0 <= c["max_retries"] <= 5):
        errs.append("max_retries must be int in [0,5]")
    if not (isinstance(c["idempotency_key"], str) and c["idempotency_key"]):
        errs.append("idempotency_key must be non-empty string")
    ac = c["acceptance_criteria"]
    if not (isinstance(ac, list) and ac):
        errs.append("acceptance_criteria must be non-empty array")
    else:
        for i, crit in enumerate(ac):
            if crit.get("type") not in CRITERION_TYPES:
                errs.append("acceptance_criteria[%d].type invalid" % i)
            if not crit.get("target"):
                errs.append("acceptance_criteria[%d].target missing" % i)
    if not (isinstance(c["evidence_required"], list) and "receipts.jsonl" in c["evidence_required"]):
        errs.append("evidence_required must include receipts.jsonl")
    nc = c["negative_constraints"]
    if not isinstance(nc, list):
        errs.append("negative_constraints must be array (empty allowed, field required)")
    else:
        for i, item in enumerate(nc):
            if not isinstance(item, dict):
                errs.append("negative_constraints[%d] must be object" % i); continue
            for f in ("id", "source_text", "rule", "acceptance_check"):
                if not (isinstance(item.get(f), str) and item[f]):
                    errs.append("negative_constraints[%d].%s must be non-empty string" % (i, f))
            if isinstance(item.get("id"), str) and not re.match(r"^NC-[0-9]{2,}$", item["id"]):
                errs.append("negative_constraints[%d].id pattern violation (NC-NN)" % i)
    if "T" not in str(c["created_at"]):
        errs.append("created_at must be ISO 8601")
    # v1.2 (2026-10-22, Phase E): optional structured actions + decision linkage.
    # Presence is enforced by the Notifier promotion path, format is checked here.
    acts = c.get("actions")
    if acts is not None:
        if not (isinstance(acts, list) and acts):
            errs.append("actions must be non-empty array when present")
        else:
            for i, a in enumerate(acts):
                if not isinstance(a, dict):
                    errs.append("actions[%d] must be object" % i); continue
                for k in ("tool", "target", "type"):
                    if not (isinstance(a.get(k), str) and a[k]):
                        errs.append("actions[%d].%s must be non-empty string" % (i, k))
    did = c.get("decision_id")
    if did is not None and not (isinstance(did, str) and did):
        errs.append("decision_id must be non-empty string when present")
    return errs

def main(paths):
    rc = 0
    for p in paths:
        try:
            c = json.load(open(p))
        except Exception as e:
            print("FAIL %s: unreadable (%s)" % (p, e)); rc = 1; continue
        errs = check(c)
        if errs:
            rc = 1
            print("FAIL %s:" % p)
            for e in errs:
                print("  - %s" % e)
        else:
            # Current 5-type contracts carry no idempotency_key/timeout_s/
            # max_retries at top level; print what exists instead of
            # raising KeyError on a PASS.
            print("PASS %s (task_id=%s task_type=%s idem=%s timeout=%s retries=%s)" %
                  (p, c.get("task_id"), c.get("task_type"), c.get("idempotency_key"),
                   c.get("timeout_s"), c.get("max_retries")))
    return rc

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
