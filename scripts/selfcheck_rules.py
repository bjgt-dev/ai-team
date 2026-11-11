#!/usr/bin/env python3
"""自查规则：只入队，不自动修复、不自动派单、不改业务状态。
R1 盘上pending/claimed数 != patrol-latest报表数
R2 根盘使用率 >85%
R3 pending任务 age >600s 未认领
队列：state/review-queue/queue.jsonl，同一规则同一证据24h内幂等。
用法：python3 selfcheck_rules.py [--patrol PATH] [--contracts-root PATH] [--queue PATH] [--disk-pct N]
"""
import argparse, hashlib, json, os, re, shutil, sys, time
from pathlib import Path
HUB = Path("/home/ubuntu/ai-team")
def parse_patrol(p):
    t = Path(p).read_text(errors="ignore")
    m = re.search(r"pending\s*合计\s*(\d+)", t)
    c = re.search(r"claimed\s*合计\s*(\d+)", t)
    return (int(m.group(1)) if m else None, int(c.group(1)) if c else None)
def count_files(root, kind):
    d = Path(root) / kind
    if not d.exists(): return 0
    n = 0
    for seat in d.iterdir():
        if seat.is_dir():
            n += sum(1 for f in seat.iterdir() if f.is_file() and f.suffix == ".json")
        elif seat.is_file() and seat.suffix == ".json":
            n += 1
    return n
def aged_pending(root, threshold=600):
    out = []
    d = Path(root) / "pending"
    if not d.exists(): return out
    now = time.time()
    for seat in d.iterdir():
        if not seat.is_dir(): continue
        for f in seat.iterdir():
            if f.is_file() and f.suffix == ".json" and now - f.stat().st_mtime > threshold:
                out.append(f"{seat.name}/{f.name}")
    return sorted(out)
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--patrol", default=str(HUB/"ceo-seat/out/patrol-latest.md"))
    ap.add_argument("--contracts-root", default=str(HUB/"contracts"))
    ap.add_argument("--queue", default=str(HUB/"state/review-queue/queue.jsonl"))
    ap.add_argument("--disk-pct", type=float, default=None)
    a = ap.parse_args()
    patrol_pending, patrol_claimed = parse_patrol(a.patrol)
    disk_pending = count_files(a.contracts_root, "pending")
    disk_claimed = count_files(a.contracts_root, "claimed")
    if a.disk_pct is not None:
        disk_pct = a.disk_pct
    else:
        u = shutil.disk_usage("/")
        disk_pct = round(u.used / u.total * 100, 1)
    aged = aged_pending(a.contracts_root, 600)
    records = []
    if patrol_pending is not None and (patrol_pending != disk_pending or patrol_claimed != disk_claimed):
        records.append(("R1", "high", f"patrol pending={patrol_pending} claimed={patrol_claimed} vs disk pending={disk_pending} claimed={disk_claimed}"))
    if disk_pct > 85:
        records.append(("R2", "medium", f"root disk {disk_pct}% >85%"))
    if aged:
        aged_str = chr(44).join(aged)
        records.append(("R3", "high", "aged pending >600s: " + aged_str))
    qpath = Path(a.queue)
    qpath.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if qpath.exists():
        for line in qpath.read_text().splitlines():
            try: existing.append(json.loads(line))
            except: pass
    now = time.time()
    added = 0
    for rule_id, severity, evidence in records:
        key = hashlib.sha256(f"{rule_id}|{evidence}".encode()).hexdigest()[:16]
        dup = False
        for r in existing:
            if r.get("idempotency_key") == key and now - r.get("_ts", 0) < 86400:
                dup = True; break
        if dup: continue
        rec = {"rule_id": rule_id, "severity": severity, "evidence": evidence, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()), "idempotency_key": key, "_ts": int(now)}
        with open(qpath, "a") as f: f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        existing.append(rec); added += 1
    print(json.dumps({"patrol_pending": patrol_pending, "patrol_claimed": patrol_claimed, "disk_pending": disk_pending, "disk_claimed": disk_claimed, "disk_pct": disk_pct, "aged": aged, "records_triggered": len(records), "added": added, "queue": str(qpath)}, ensure_ascii=False))
    return 0
if __name__ == "__main__":
    sys.exit(main())
