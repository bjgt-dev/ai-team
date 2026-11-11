#!/usr/bin/env python3
"""台账计数核对（2026-11-04，InstaBox 漏 3、4 号教训）。
比对：state/.instabox*-api-key 文件数 vs secrets-registry.md 里 InstaBox key xN。
不一致即报错，不改生产，不发通知（由调用方决定是否报）。
"""
import glob, re, pathlib
files = glob.glob("/home/ubuntu/ai-team/state/.instabox*-api-key")
reg = pathlib.Path("/home/ubuntu/ai-team/state/secrets-registry.md").read_text()
m = re.search(r"InstaBox key x(\d+)", reg)
reg_n = int(m.group(1)) if m else -1
print(f"key_files={len(files)} registry_x={reg_n} match={len(files)==reg_n}")
raise SystemExit(0 if len(files)==reg_n else 1)
