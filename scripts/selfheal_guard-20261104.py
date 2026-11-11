#!/usr/bin/env python3
"""Order14 self-heal boundary guard (policy, no live actions).
AUTO allowed ONLY if ALL hold: idempotent, retryable, no external side
effect, type in (doc, script, patrol, timer), retry_count <= 3 with backoff.
DENY (requires human or butler decision) for: external publish, money,
delete/irreversible, non-whitelist production config, permission change,
or any action failing idempotent/retryable/no-side-effect.
Backoff schedule for allowed retries: 30s, 120s, 300s (attempt 1/2/3).
Usage: echo '{"type":"script","idempotent":true,...}' | python3 selfheal_guard-20261104.py
  --test runs the built-in case table and prints PASS/FAIL per case.
"""
import argparse, json, sys
AUTO_TYPES = {"doc", "script", "patrol", "timer"}
DENY_CATEGORIES = {"publish", "money", "delete", "irreversible", "prod_config_nonwhitelist", "permission"}
BACKOFF = [30, 120, 300]

def decide(a):
    cat = a.get("category", "")
    if cat in DENY_CATEGORIES:
        return {"decision": "DENY", "reason": "category requires human/butler decision: " + cat}
    if a.get("type") not in AUTO_TYPES:
        return {"decision": "DENY", "reason": "type not in auto allowlist"}
    if not (a.get("idempotent") and a.get("retryable") and not a.get("external_side_effect")):
        return {"decision": "DENY", "reason": "must be idempotent, retryable, no external side effect"}
    rc = int(a.get("retry_count", 0))
    if rc > 3:
        return {"decision": "DENY", "reason": "retry_count exceeds 3"}
    return {"decision": "AUTO", "reason": "within self-heal boundary",
            "next_backoff_s": BACKOFF[min(rc, 2)] if rc < 3 else None}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    a = ap.parse_args()
    if a.test:
        cases = [
            ({"type": "script", "idempotent": True, "retryable": True, "external_side_effect": False, "retry_count": 0}, "AUTO"),
            ({"type": "timer", "idempotent": True, "retryable": True, "external_side_effect": False, "retry_count": 3}, "AUTO"),
            ({"type": "script", "idempotent": True, "retryable": True, "external_side_effect": False, "retry_count": 4}, "DENY"),
            ({"type": "doc", "idempotent": False, "retryable": True, "external_side_effect": False}, "DENY"),
            ({"type": "script", "idempotent": True, "retryable": True, "external_side_effect": True}, "DENY"),
            ({"type": "script", "category": "publish", "idempotent": True, "retryable": True, "external_side_effect": False}, "DENY"),
            ({"type": "script", "category": "money", "idempotent": True, "retryable": True, "external_side_effect": False}, "DENY"),
            ({"type": "script", "category": "delete", "idempotent": True, "retryable": True, "external_side_effect": False}, "DENY"),
            ({"type": "script", "category": "prod_config_nonwhitelist", "idempotent": True, "retryable": True, "external_side_effect": False}, "DENY"),
            ({"type": "script", "category": "permission", "idempotent": True, "retryable": True, "external_side_effect": False}, "DENY"),
            ({"type": "code", "idempotent": True, "retryable": True, "external_side_effect": False}, "DENY"),
        ]
        results = []
        for i, (inp, want) in enumerate(cases):
            got = decide(inp)["decision"]
            results.append({"case": i, "want": want, "got": got, "pass": got == want})
        ok = all(r["pass"] for r in results)
        print(json.dumps({"ok": ok, "cases": results}, ensure_ascii=False, indent=2))
        return 0 if ok else 1
    data = json.load(sys.stdin)
    print(json.dumps(decide(data), ensure_ascii=False))
    return 0

if __name__ == "__main__":
    sys.exit(main())
