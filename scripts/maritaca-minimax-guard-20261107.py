#!/usr/bin/env python3
"""Maritaca + MiniMax channel guard (2026-11-07, gap-fill).

GAP IT CLOSES: gateway channels 28 (maritaca-s5-free, verified working
2026-11-06) and 26 (minimax-m3-flash, quota exhausted + disabled the
same day) had NO guard code anywhere on the hub -- no quota/expiry
watch, no status check, no re-enable rule. OpenRouter 15/16/17 and
Bailian both have guards; these two did not.

Verified live 2026-11-07 17:5x CST via newapi DB:
  channel 26 minimax-m3-flash  status 0 (disabled, quota exhausted)
  channel 28 maritaca-s5-free  status 1 (active, in test pool only)
  channel 29 CF Workers AI     status 1 (info only here)

Rules (report-only, NEVER auto-enables/disables -- a re-enable needs
a human verification that quota actually returned):
  - 28 must be status 1; if 0 -> ALERT + exit 1.
  - 26 must stay status 0; if 1 -> WARN "re-enabled without
    re-verification" + exit 1 (someone/something re-enabled it).
  - Writes state/maritaca-minimax-guard-20261107/latest.json; on
    breach also writes one outbox alert file.
Live DB read uses the same docker-exec mysql pattern as
scripts/openrouter-rotation-guard.py (keys are never printed).
--channels-json accepts a stub {"26": 0, "28": 1} for tests/offline.
Exit codes: 0 ok, 1 breach, 2 cannot determine channel status.
"""
import argparse
import json
import os
import subprocess
import sys
import time

BASE = os.environ.get("AI_TEAM_BASE", "/home/ubuntu/ai-team")
STATE_DIR = os.path.join(BASE, "state", "maritaca-minimax-guard-20261107")
OUTBOX_DIR = os.path.join(BASE, "state", "outbox")
EXPECTED = {28: 1, 26: 0}  # maritaca active, minimax disabled
NAMES = {28: "maritaca-s5-free", 26: "minimax-m3-flash"}


def live_statuses():
    sql = ("SELECT id, status FROM channels WHERE id IN (26,28,29);")
    with open("/tmp/mm_guard_q.sql", "w") as f:
        f.write(sql)
    try:
        with open("/tmp/mm_guard_q.sql") as fin:
            r = subprocess.run(
                ["docker", "exec", "-i", "ai-stack-new-api-db-1", "sh", "-c",
                 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N oneapi'],
                stdin=fin, capture_output=True, text=True, timeout=60)
    finally:
        try:
            os.remove("/tmp/mm_guard_q.sql")
        except OSError:
            pass
    out = {}
    for line in r.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].strip().isdigit():
            out[int(parts[0])] = int(parts[1])
    return out


def main():
    ap = argparse.ArgumentParser(description="Maritaca + MiniMax guard")
    ap.add_argument("--channels-json", default=None,
                    help='stub for tests, e.g. {"26": 0, "28": 1}')
    ap.add_argument("--state-dir", default=STATE_DIR)
    ap.add_argument("--no-outbox", action="store_true")
    args = ap.parse_args()

    if args.channels_json:
        try:
            raw = json.loads(args.channels_json)
            statuses = {int(k): int(v) for k, v in raw.items()}
        except Exception as e:
            print(json.dumps({"ok": False, "error": "bad stub: %s" % e}))
            return 2
    else:
        statuses = live_statuses()

    problems, notes = [], []
    for cid, want in EXPECTED.items():
        got = statuses.get(cid)
        if got is None:
            problems.append("channel %d (%s) status unknown" % (cid, NAMES[cid]))
        elif got != want:
            if cid == 28:
                problems.append("channel 28 maritaca-s5-free status=%d, want 1 (verified working 2026-11-06)" % got)
            else:
                problems.append("channel 26 minimax-m3-flash status=%d, want 0 (quota exhausted 2026-11-06; re-enable needs re-verification)" % got)
        else:
            notes.append("channel %d %s status=%d as expected" % (cid, NAMES[cid], got))
    if 29 in statuses:
        notes.append("channel 29 CF Workers AI status=%d (info only)" % statuses[29])

    ok = not problems
    rec = {"ok": ok, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "statuses": {str(k): v for k, v in sorted(statuses.items())},
           "problems": problems, "notes": notes}
    os.makedirs(args.state_dir, exist_ok=True)
    with open(os.path.join(args.state_dir, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    if not ok and not args.no_outbox:
        os.makedirs(OUTBOX_DIR, exist_ok=True)
        fn = os.path.join(OUTBOX_DIR, "maritaca-minimax-guard-%s.txt"
                          % time.strftime("%Y%m%d-%H%M%S"))
        with open(fn, "w", encoding="utf-8") as f:
            f.write("Maritaca/MiniMax guard breach:\n" + "\n".join(problems) + "\n")
        rec["outbox"] = fn
    print(json.dumps(rec, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
