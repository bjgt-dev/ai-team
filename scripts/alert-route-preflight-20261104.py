#!/usr/bin/env python3
"""Alert-route preflight (DRAFT 2026-11-04, NOT wired into production).

Rule: an incident with no hub landing is NOT an alert.
  --check <id>: that incident's JSON exists, its twt-inbox file exists,
    and all required fields are present. Any failure -> exit 2.
  --scan: every pending incident JSON must have an inbox file, and every
    inbox file must have a JSON twin. Any failure -> exit 2.
  --selftest: runs the same logic in a temp dir (good pair passes,
    JSON-without-inbox fails). Exit 0 iff both behave as documented.
This script does NOT touch the C2 executor, verifier, guard, or any
production submitter, and does NOT modify the single-seat preflight
(script twt-single-seat-preflight-20261104.py, whose 8/8 selftest stays intact).
"""
import argparse, json, sys, tempfile
from pathlib import Path

REQUIRED = ("id", "created_at", "type", "level", "default_action", "needs_chairman_approval")

def base_dir():
    return Path(__file__).resolve().parent.parent

def check_pair(base, iid):
    """Return list of failure strings (empty = pass)."""
    fails = []
    jpath = base / "state" / "incidents" / f"{iid}.json"
    mpath = base / "shared" / "twt2026-inbox" / f"{iid}.md"
    if not jpath.is_file():
        fails.append(f"{iid}: missing incident JSON state/incidents/{iid}.json")
        return fails
    try:
        r = json.loads(jpath.read_text(encoding="utf-8"))
    except Exception as e:
        return [f"{iid}: unreadable incident JSON: {e}"]
    for f in REQUIRED:
        if f not in r or r[f] in (None, ""):
            fails.append(f"{iid}: missing required field {f}")
    if r.get("tg_role", "backup-only") != "backup-only":
        fails.append(f"{iid}: tg_role is not backup-only")
    if not mpath.is_file():
        fails.append(f"{iid}: missing twt inbox file shared/twt2026-inbox/{iid}.md")
    return fails

def scan(base):
    inc_dir = base / "state" / "incidents"
    inbox_dir = base / "shared" / "twt2026-inbox"
    fails = []
    if not inc_dir.is_dir():
        return ["state/incidents/ missing on hub"]
    pending = []
    for p in sorted(inc_dir.glob("*.json")):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            fails.append(f"{p.name}: unreadable: {e}")
            continue
        if r.get("status") == "pending":
            pending.append(r.get("id") or p.stem)
    for iid in pending:
        fails.extend(check_pair(base, iid))
    if inbox_dir.is_dir():
        known = {p.stem for p in inc_dir.glob("*.json")}
        for m in sorted(inbox_dir.glob("*.md")):
            if m.stem not in known:
                fails.append(f"{m.stem}: orphan inbox file, no incident JSON")
    else:
        if pending:
            fails.append("shared/twt2026-inbox/ missing while pending incidents exist")
    return fails

def selftest():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        (base / "state" / "incidents").mkdir(parents=True)
        (base / "shared" / "twt2026-inbox").mkdir(parents=True)
        good = {"id": "GOOD-1", "created_at": "2026-11-04T23:00:00+08:00",
                "type": "incident", "level": "P1", "default_action": "check and close",
                "needs_chairman_approval": False, "status": "pending", "tg_role": "backup-only"}
        (base / "state" / "incidents" / "GOOD-1.json").write_text(json.dumps(good), encoding="utf-8")
        (base / "shared" / "twt2026-inbox" / "GOOD-1.md").write_text("# GOOD-1\n", encoding="utf-8")
        bad = dict(good, id="BAD-1")
        (base / "state" / "incidents" / "BAD-1.json").write_text(json.dumps(bad), encoding="utf-8")
        # BAD-1 deliberately has no inbox file.
        if check_pair(base, "GOOD-1"):
            print("SELFTEST FAIL: good pair should pass")
            return 2
        if not check_pair(base, "BAD-1"):
            print("SELFTEST FAIL: JSON-without-inbox should fail")
            return 2
        fails = scan(base)
        if not any("BAD-1" in f for f in fails):
            print("SELFTEST FAIL: scan should flag BAD-1")
            return 2
    print("SELFTEST PASS: good pair passes, JSON-without-inbox fails (exit 2)")
    return 0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=None)
    ap.add_argument("--check", default=None)
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    base = Path(args.base) if args.base else base_dir()
    if args.check:
        fails = check_pair(base, args.check)
    elif args.scan:
        fails = scan(base)
    else:
        ap.error("need --check <id>, --scan, or --selftest")
    if fails:
        for f in fails:
            print(f"ALERT-ROUTE FAIL: {f}", file=sys.stderr)
        return 2
    print("ALERT-ROUTE PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
