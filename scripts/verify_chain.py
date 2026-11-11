#!/usr/bin/env python3
import json, hashlib, sys, pathlib
LOG = pathlib.Path("/home/hatch/workspace/state/harness-append-only.log")
prev = "0"*64; n = 0
for line in LOG.read_text(encoding="utf-8").strip().splitlines():
    e = json.loads(line)
    if e["prev"] != prev:
        print(f"CHAIN_BROKEN at line {n+1}: prev mismatch"); sys.exit(2)
    body = f"{e['ts']}|{e['task']}|{e['action']}|{e['prev']}"
    if hashlib.sha256(body.encode()).hexdigest() != e["hash"]:
        print(f"CHAIN_BROKEN at line {n+1}: hash mismatch"); sys.exit(2)
    prev = e["hash"]; n += 1
print(f"CHAIN_OK lines={n} last={prev[:12]}")
