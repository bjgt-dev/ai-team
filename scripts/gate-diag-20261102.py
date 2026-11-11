#!/usr/bin/env python3
"""Show the raw model reply to the exact probe-gate prompt (2026-11-02).

Diagnostic only: posts the same prompt butler_decide.model_call uses for
the PROBE pack and prints the raw reply text (truncated). No state is
written, no switch is flipped, no credential is printed.
"""
import json
import sys
import urllib.request

CFG = "/home/ubuntu/.config/opencode/opencode.json"

PROMPT = ("Decide strictly in JSON {verdict, level, conditions, rationale}. "
          "verdict in DONE/FAILED/DEAD/NO_CHANGE/DECIDE; level in L0/L1/L2.\nPROBE")


def main():
    cfg = json.load(open(CFG))
    opts = cfg["provider"]["my-gateway"]["options"]
    url = str(opts["baseURL"]).rstrip("/")
    if not url.endswith("/v1"):
        url = url + "/v1"
    body = json.dumps({"model": "glm-4-flash-cc",
                       "messages": [{"role": "user", "content": PROMPT}],
                       "temperature": 0}).encode()
    req = urllib.request.Request(
        url + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + str(opts["apiKey"]),
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        d = json.load(resp)
    text = d["choices"][0]["message"]["content"]
    print("RAW_REPLY_BEGIN")
    print(text[:500])
    print("RAW_REPLY_END")
    try:
        json.loads(text)
        print("PARSE ok")
    except Exception as e:  # noqa: BLE001 - diagnostic reports type only
        print("PARSE fail:", type(e).__name__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
