#!/usr/bin/env python3
"""Observe-correct loop (methodology: 执行-观察-修正闭环).
Scans QUARANTINED central_tasks, groups by normalized title signature.
A signature with >=2 quarantined tasks is a repeat failure: write a
reflection and add it to the blocklist. Re-dispatch of a blocked signature
is refused by taskcontract_gate.py (it reads the same blocklist file).
Fail-closed: if the DB is unreachable, exit 2 and do nothing.
"""
import json, os, re, subprocess, sys
from datetime import datetime

DSN_FILE = "/home/ubuntu/ai-team/state/.cq-dsn"
STATE_DIR = "/home/ubuntu/ai-team/state/observe-correct-20261109"
BLOCKLIST = os.path.join(STATE_DIR, "blocked-signatures.json")
REFLECT_DIR = os.path.join(STATE_DIR, "reflections")
THRESHOLD = 2

def dsn():
    # .cq-dsn holds: CQ_DSN="host=... port=... dbname=... user=... password=..."
    raw = open(DSN_FILE).read().strip()
    m = re.search(r'CQ_DSN="([^"]+)"', raw)
    if m:
        return m.group(1)
    m = re.search(r"CQ_DSN='([^']+)'", raw)
    if m:
        return m.group(1)
    return raw

def psql(sql):
    r = subprocess.run(["psql", dsn(), "-t", "-A", "-F", "\t", "-c", sql],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError("psql failed: " + r.stderr[:200])
    return r.stdout

def normalize(title):
    # Strip trailing -N, :suffix, and UUID-ish tails; collapse to a signature.
    t = (title or "").strip()
    t = re.sub(r"-[0-9a-f]{8}-[0-9a-f-]+$", "", t)
    t = re.sub(r"-[0-9]+$", "", t)
    t = re.sub(r":.*$", "", t)
    return t or "unknown"

def main():
    os.makedirs(REFLECT_DIR, exist_ok=True)
    blocked = {}
    if os.path.exists(BLOCKLIST):
        blocked = json.load(open(BLOCKLIST))
    rows = psql(
        "SELECT task_id, title FROM central_tasks WHERE status='QUARANTINED';")
    groups = {}
    for line in rows.strip().split("\n"):
        if not line.strip():
            continue
        tid, _, title = line.partition("\t")
        sig = normalize(title)
        groups.setdefault(sig, []).append((tid.strip(), title.strip()))
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    new_blocks = []
    for sig, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(items) < THRESHOLD:
            continue
        if sig in blocked:
            continue
        # Write reflection.
        ref_path = os.path.join(REFLECT_DIR, "reflection-%s-%s.md" % (
            re.sub(r"[^A-Za-z0-9_-]+", "_", sig)[:60],
            datetime.now().strftime("%Y%m%d")))
        with open(ref_path, "w", encoding="utf-8") as f:
            f.write("# Reflection: repeat failure `%s`\n\n" % sig)
            f.write("- detected: %s\n- quarantined tasks: %d\n\n" % (now, len(items)))
            for tid, title in items:
                f.write("- %s `%s`\n" % (tid, title))
            f.write("\n## What to fix\n\n")
            f.write("1. Find the root cause shared by these %d failures (same signature).\n" % len(items))
            f.write("2. Fix the prompt, contract, or code that produces this failure.\n")
            f.write("3. Remove this signature from blocked-signatures.json ONLY after a canary of the fixed path passes.\n")
            f.write("4. Do not re-dispatch this kind of task until the block is cleared.\n")
        blocked[sig] = {"count": len(items), "first_seen": now,
                        "reflection": ref_path, "tasks": [t for t, _ in items]}
        new_blocks.append(sig)
        print("BLOCKED %s count=%d reflection=%s" % (sig, len(items), ref_path))
    with open(BLOCKLIST, "w", encoding="utf-8") as f:
        json.dump(blocked, f, ensure_ascii=False, indent=2)
    print("groups>=%d: %d, new blocks: %d, total blocked: %d"
          % (THRESHOLD, sum(1 for v in groups.values() if len(v) >= THRESHOLD),
             len(new_blocks), len(blocked)))
    return 0

if __name__ == "__main__":
    sys.exit(main())
