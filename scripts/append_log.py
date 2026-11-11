#!/usr/bin/env python3
"""第三层：只追加日志 + 哈希链。用法：append_log.py <task> <action> ；verify_chain.py 检查链。
每条：时间 | task | action | prev_hash | hash（本条前三字段+prev_hash 的 sha256）。
改写、删除、插入任意一条都会断链，verify 即报错。
"""
import sys, hashlib, json, datetime, pathlib
LOG = pathlib.Path("/home/hatch/workspace/state/harness-append-only.log")
def prev_hash():
    if not LOG.exists() or LOG.stat().st_size == 0:
        return "0"*64
    lines = LOG.read_text(encoding="utf-8").strip().splitlines()
    return json.loads(lines[-1])["hash"]
def main():
    task, action = sys.argv[1], sys.argv[2]
    ph = prev_hash()
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    body = f"{ts}|{task}|{action}|{ph}"
    h = hashlib.sha256(body.encode()).hexdigest()
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts":ts,"task":task,"action":action,"prev":ph,"hash":h}, ensure_ascii=False) + "\n")
    print("APPENDED " + h[:12])
if __name__ == "__main__":
    main()
