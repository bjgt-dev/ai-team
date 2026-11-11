#!/usr/bin/env python3
"""Standalone probe-gate check for the butler role flip (2026-11-02).

Runs butler_decide.probe_gate() with REAL model names against the hub's
own gateway, WITHOUT flipping BUTLER_API and without writing any ledger
or alert state. Credentials are read from the hub's opencode config into
env only and are NEVER printed. On gate failure, one diagnostic call
prints the raw model reply (truncated) so the failure reason is visible.
"""
import json
import os
import sys

sys.path.insert(0, "/home/ubuntu/ai-team/scripts")

CFG = "/home/ubuntu/.config/opencode/opencode.json"


def main():
    cfg = json.load(open(CFG))
    opts = cfg["provider"]["my-gateway"]["options"]
    url = str(opts["baseURL"]).rstrip("/")
    if not url.endswith("/v1"):
        url = url + "/v1"
    os.environ["BUTLER_GATEWAY_URL"] = url
    os.environ["BUTLER_GATEWAY_KEY"] = str(opts["apiKey"])

    import butler_decide

    ok = butler_decide.probe_gate("glm-4-flash-cc", "glm-4-flash")
    print("GATE_RESULT", "PASS" if ok else "FAIL")
    if not ok:
        try:
            out, used = butler_decide.model_call("PROBE", "glm-4-flash-cc")
            print("DIAG model_call returned:", json.dumps(out, ensure_ascii=False)[:300])
            good, reason = butler_decide.validate_model_output(out)
            print("DIAG schema:", "ok" if good else reason)
        except Exception as e:  # noqa: BLE001 - diagnostic reports type only
            print("DIAG model_call raised:", type(e).__name__)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
