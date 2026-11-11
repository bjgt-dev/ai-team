#!/usr/bin/env python3
# sort-log 安全追加：从文件读一个 JSON 对象，校验后以 jsonl 一行追加。禁止再用 python -c 内联拼 JSON。
import json, sys
src = sys.argv[1] if len(sys.argv) > 1 else "-"
raw = sys.stdin.read() if src == "-" else open(src).read()
obj = json.loads(raw)
line = json.dumps(obj, ensure_ascii=False)
path = "/home/ubuntu/ai-team/state/priority/sort-log.jsonl"
with open(path, "a") as f:
    f.write(line + "\n")
print("SORTLOG_OK appended")
