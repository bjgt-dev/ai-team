#!/usr/bin/env python3
"""第一层：闸门校验器。比对 gate-authority.json 哈希清单，任一闸门文件被改即 exit 2。
用法：python3 gate_verify.py
注意：444 是防误改，root 仍可 chmod 改写，所以本脚本是“篡改可检出”，不是“物理上改不动”。
"""
import json, hashlib, pathlib, sys
manifest = json.load(open("/home/hatch/workspace/state/gate-authority.json", encoding="utf-8"))
bad = []
for path, want in manifest["files"].items():
    try:
        got = hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
    except Exception as e:
        bad.append(path + " unreadable " + str(e)); continue
    if got != want:
        bad.append(path + " hash mismatch")
if bad:
    print("GATE_TAMPERED: " + "; ".join(bad)); sys.exit(2)
print("GATE_OK: all gate files match chairman manifest")
