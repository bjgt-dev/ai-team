#!/usr/bin/env python3
"""Map the frozen review_gate PASS/FAIL verdict to PASS/REVISE/BLOCKED.

P0 baseline 2026-10-23 requires the on-demand review layer to output three
states. review_gate.py and review_config.json are frozen, so this wrapper does
not edit them; it invokes review_gate.py and classifies its result:

  PASS   -> PASS
  FAIL   -> REVISE (artifact exists but content needs correction)
  FAIL with missing artifact / review service error / unparseable output
         -> BLOCKED (human or infrastructure action required)
"""
import json
import os
import subprocess
import sys

TEAM_ROOT = os.environ.get("TEAM_ROOT", "/home/ubuntu/ai-team")
GATE = os.path.join(TEAM_ROOT, "scripts", "review_gate.py")
BLOCKED_MARKERS = (
    "no reviewable artifact",
    "review service error",
    "review parse failed",
    "timeout",
    "timed out",
    "unavailable",
)


def classify(raw):
    reasons = raw.get("reasons") or []
    if not isinstance(reasons, list):
        reasons = [str(reasons)]
    verdict = str(raw.get("verdict", "")).upper()
    text = " ".join(str(r) for r in reasons).lower()
    if verdict == "PASS":
        return "PASS", reasons
    if verdict == "FAIL" and any(m in text for m in BLOCKED_MARKERS):
        return "BLOCKED", reasons
    if verdict == "FAIL":
        return "REVISE", reasons
    return "BLOCKED", reasons + [f"unknown review verdict: {verdict or 'missing'}"]


def main():
    if len(sys.argv) != 3:
        print(json.dumps({"verdict": "BLOCKED", "reasons": ["usage: review_tristate.py <bundle_dir> <task_id>"]}))
        return 0
    try:
        proc = subprocess.run(
            [sys.executable, GATE, sys.argv[1], sys.argv[2]],
            capture_output=True, text=True, timeout=180,
        )
        try:
            raw = json.loads(proc.stdout)
        except Exception:
            return print(json.dumps({
                "verdict": "BLOCKED",
                "reasons": [f"review_gate non-json output: {proc.stdout[:200]}"],
            }))
        verdict, reasons = classify(raw)
        print(json.dumps({
            "verdict": verdict,
            "reasons": reasons,
            "source_verdict": raw.get("verdict"),
        }, ensure_ascii=False))
    except subprocess.TimeoutExpired:
        print(json.dumps({"verdict": "BLOCKED", "reasons": ["review_gate timeout"]}))
    except Exception as exc:
        print(json.dumps({"verdict": "BLOCKED", "reasons": [f"review_tristate error: {type(exc).__name__}: {exc}"][:1]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
