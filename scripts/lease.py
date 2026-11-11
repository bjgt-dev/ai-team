#!/usr/bin/env python3
# Lease + heartbeat tool for pull-based claiming (2026-10-20 supervisor ruling).
# Hub-side state: contracts/leases/<task_id>.json, state/heartbeats/<worker>.json,
# state/executed/<idempotency_key>.done (dedupe ledger).
# Usage:
#   lease.py claim <task_id> <worker> <idem_key> <lease_s>
#   lease.py heartbeat <task_id>
#   lease.py release <task_id>
#   lease.py workerping <worker>
#   lease.py executed <idem_key>
#   lease.py check-executed <idem_key>   (exit 0 if already executed)
import json, os, sys, time

ROOT = "/home/ubuntu/ai-team"
LEASES = os.path.join(ROOT, "contracts", "leases")
HEARTBEATS = os.path.join(ROOT, "state", "heartbeats")
EXECUTED = os.path.join(ROOT, "state", "executed")

def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)

def main(argv):
    if len(argv) < 2:
        print("usage: lease.py <cmd> ..."); return 2
    cmd = argv[1]
    now = int(time.time())
    if cmd == "claim":
        task_id, worker, idem, lease_s = argv[2], argv[3], argv[4], int(argv[5])
        _write(os.path.join(LEASES, task_id + ".json"),
               {"task_id": task_id, "worker": worker, "idempotency_key": idem,
                "claimed_at": now, "lease_expires_at": now + lease_s, "heartbeat_at": now})
        print("LEASED %s -> %s (%ss)" % (task_id, worker, lease_s)); return 0
    if cmd == "heartbeat":
        p = os.path.join(LEASES, argv[2] + ".json")
        try:
            d = json.load(open(p))
        except Exception:
            print("NO_LEASE"); return 1
        d["heartbeat_at"] = now
        _write(p, d); print("HB %s" % argv[2]); return 0
    if cmd == "release":
        p = os.path.join(LEASES, argv[2] + ".json")
        if os.path.exists(p): os.remove(p)
        print("RELEASED %s" % argv[2]); return 0
    if cmd == "workerping":
        _write(os.path.join(HEARTBEATS, argv[2] + ".json"),
               {"worker": argv[2], "ts": now})
        print("PING %s" % argv[2]); return 0
    if cmd == "executed":
        os.makedirs(EXECUTED, exist_ok=True)
        open(os.path.join(EXECUTED, argv[2] + ".done"), "w").write(str(now))
        print("EXECUTED_MARKED %s" % argv[2]); return 0
    if cmd == "check-executed":
        return 0 if os.path.exists(os.path.join(EXECUTED, argv[2] + ".done")) else 1
    print("unknown cmd"); return 2

if __name__ == "__main__":
    sys.exit(main(sys.argv))
