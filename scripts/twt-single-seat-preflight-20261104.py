#!/usr/bin/env python3
"""Single-seat gate (2026-11-04, chairman order).
Rule: only twt/muse-1 is the interface; muse/muse-2 are external disks only;
execution goes only to the InstaBox 12 main-worker pool (or hub timer / C2).
This script is a GATE, not a submitter: it writes nothing to the queue.
Caller must check exit code BEFORE any INSERT: 0 = pass, 2 = reject.
Reject triggers (binding fields only, not free-text notes):
  - payload.worker / executor / executor_pool / assignee / owner /
    target_session / worker_id equals or contains "muse" (except muse-1),
    "muse-2", or "noesis" as a person/session binding.
Allowed (explicit):
  - owner / executor_pool = twt, muse-1, twt/muse-1, any-reachable-main-worker,
    instabox-main-workers, main-worker-pool, hub-timer, c2-executor.
  - type=timer with no worker field (hub timer, not a person).
  - muse_pull as a DISK channel (read-only pull + SHA verify), never as executor.
  - A free-text note naming a machine as a credential LOCATION (e.g. an
    existing ~/.oci key on a Noesis machine) does NOT reject, provided the
    binding fields above point at the main-worker pool. That is a pointer,
    not a person binding. The OCI task is the standing example.
Usage:
  --payload-json '{"type":"script","worker":"muse"}' [--title TITLE]
  --payload-file /path/to/payload.json [--title TITLE]
  --selftest   (8 cases, no DB, exit 0 iff all behave as documented)
"""
import json, sys

ALLOWED_POOLS = {
    "twt", "muse-1", "twt/muse-1", "any-reachable-main-worker",
    "instabox-main-workers", "main-worker-pool", "hub-timer", "c2-executor",
}
BINDING_FIELDS = ("worker", "executor", "executor_pool", "assignee", "owner",
                  "target_session", "worker_id", "session")

def _norm(v):
    return str(v).strip().lower() if v is not None else ""

def _is_person_binding(value):
    v = _norm(value)
    if not v:
        return False
    if v in ALLOWED_POOLS:
        return False
    if v in ("muse-1", "twt-muse-1") or v.startswith("muse-1-"):
        return False
    if "muse-2" in v or "noesis" in v:
        return True
    if v == "muse" or v.startswith("muse-") or v.startswith("muse_"):
        return True
    return False

def check(title, payload):
    reasons = []
    if not isinstance(payload, dict):
        return ["payload is not a JSON object"]
    for f in BINDING_FIELDS:
        if f in payload and _is_person_binding(payload.get(f)):
            reasons.append("binding field %s=%r binds a single muse/noesis session" % (f, payload.get(f)))
    t = _norm(title)
    if t.startswith("ws2-storage-muse") and "muse-1" not in t:
        reasons.append("title %r is a per-session muse/muse-2 disk task, not a twt-owned task" % (title,))
    ptype = _norm(payload.get("type"))
    if ptype in ("disk-verify", "disk_backup", "storage") and payload.get("owner") not in (None, "twt", "muse-1", "twt/muse-1"):
        reasons.append("disk task owner must be twt/muse-1")
    return reasons

def main(argv):
    title, payload, selftest = "", None, False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--title" and i + 1 < len(argv):
            title, i = argv[i + 1], i + 2
        elif a == "--payload-json" and i + 1 < len(argv):
            payload, i = json.loads(argv[i + 1]), i + 2
        elif a == "--payload-file" and i + 1 < len(argv):
            with open(argv[i + 1], encoding="utf-8") as fh:
                payload, i = json.load(fh), i + 2
        elif a == "--selftest":
            selftest, i = True, i + 1
        else:
            print("preflight: unknown arg %s" % a, file=sys.stderr); return 2
    if selftest:
        cases = [
            ("oci-7d-p95-main-worker", {"type": "code", "worker": "any-reachable-main-worker", "executor_pool": "any-reachable-main-worker", "note": "key lives on a Noesis machine, pointer only"}, True, "main-worker pool passes, credential-location note does not reject"),
            ("x", {"type": "script", "worker": "muse"}, False, "worker=muse rejects"),
            ("x", {"type": "script", "worker": "muse-2"}, False, "worker=muse-2 rejects"),
            ("x", {"type": "script", "assignee": "noesis"}, False, "assignee=noesis rejects"),
            ("x", {"type": "script", "owner": "twt/muse-1", "worker": "any-reachable-main-worker"}, True, "twt/muse-1 owner + main-worker passes"),
            ("timer-storage_confirm", {"type": "timer"}, True, "timer with no worker passes (hub timer)"),
            ("muse-disk-verify-twt-20261104", {"type": "script", "owner": "twt/muse-1", "channel": "muse_pull", "command": "sha256sum /data/export/manifest.json"}, True, "twt-owned muse_pull disk verify passes (disk channel, not executor)"),
            ("ws2-storage-muse", {"type": "script", "owner": "twt/muse-1"}, False, "per-session muse disk title rejects even with twt owner"),
        ]
        bad = 0
        for title_c, pay_c, want, label in cases:
            got = check(title_c, pay_c)
            ok = (not got) == want
            print(("PASS " if ok else "FAIL ") + label + ("" if ok else " -> %s" % (got,)))
            bad += 0 if ok else 1
        print("preflight selftest: %d/%d" % (len(cases) - bad, len(cases)))
        return 0 if bad == 0 else 2
    if payload is None:
        print("preflight: need --payload-json or --payload-file", file=sys.stderr); return 2
    reasons = check(title, payload)
    if reasons:
        print("REJECT: " + "; ".join(reasons)); return 2
    print("PASS: single-seat gate passed (twt only, main-worker pool, muse disk-only)")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
