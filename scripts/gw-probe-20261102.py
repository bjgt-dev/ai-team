#!/usr/bin/env python3
"""Gateway probe pre-check for the butler role flip (2026-11-02).

Reads the hub's own opencode config for the gateway baseURL/key and makes
two tiny chat completions. NEVER prints any credential value: only the
model name, the attempt number, and the model's short reply (or the error
type). A PASS here means the gateway fallback route answers; it does not
flip BUTLER_API and does not write any state.
"""
import json
import sys
import urllib.request

CFG = "/home/ubuntu/.config/opencode/opencode.json"


def find_gateway(o):
    if isinstance(o, dict):
        u = str(o.get("baseURL") or o.get("baseUrl") or o.get("base_url") or "")
        k = o.get("apiKey") or o.get("api_key") or ""
        if "3001" in u and k:
            if not u.rstrip("/").endswith("/v1"):
                u = u.rstrip("/") + "/v1"
            return (u.rstrip("/"), k)
        for v in o.values():
            r = find_gateway(v)
            if r[0]:
                return r
    elif isinstance(o, list):
        for v in o:
            r = find_gateway(v)
            if r[0]:
                return r
    return (None, None)


def call(base, key, model):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: PROBE_OK"}],
        "max_tokens": 10, "temperature": 0}).encode()
    req = urllib.request.Request(
        base + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        d = json.load(resp)
    return d["choices"][0]["message"]["content"]


def main():
    cfg = json.load(open(CFG))
    base, key = find_gateway(cfg)
    if not base:
        print("PROBE_RESULT FAIL gateway_not_found_in_config")
        return 2
    ok = False
    for model in ("glm-4-flash-cc", "glm-4-flash"):
        for i in (1, 2):
            try:
                out = call(base, key, model)
                print(f"PROBE model={model} try={i} reply={out!r}")
                ok = True
                break
            except Exception as e:  # noqa: BLE001 - probe reports type only
                print(f"PROBE model={model} try={i} ERROR {type(e).__name__}")
        if ok:
            break
    print("PROBE_RESULT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
