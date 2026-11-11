#!/usr/bin/env python3
"""hub-registry-check.py — registry vs reality drift detector (read-only).

Pattern: method-05 check_registry.py (sha/existence checks, EXPIRED rule)
+ crontab-as-code drift-check.py (receipt log, outbox alert on new drift).
Checks state/hub-registry.json against the live hub:
  1. registry parses; verified_at <= 7 days old (else EXPIRED).
  2. every registered directory exists.
  3. every registered tool path exists.
  4. every canonical path exists.
  5. every registered cron command appears in live `crontab -l`.
  6. services live: beszel process, gateway container, postgres SELECT 1.
  7. verdict sanity: warn if the legacy verdict dir holds a file newer
     than the newest file in the canonical dir (writes going wrong way).
Writes a receipt line to state/logs/hub-registry-drift.log on every run.
When the drift fingerprint differs from the last receipt AND drift is
non-empty, writes one outbox alert (state/outbox/hub-registry-drift-*.md).
Exit 0 = pass (warns allowed), 1 = any FAIL/EXPIRED, 2 = registry unreadable.
Stdlib only. No writes except the receipt + the one outbox alert.
"""
import glob
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

BASE = "/home/ubuntu/ai-team"
REG = os.path.join(BASE, "state", "hub-registry.json")
RECEIPT = os.path.join(BASE, "state", "logs", "hub-registry-drift.log")
OUTBOX = os.path.join(BASE, "state", "outbox")
CST = timezone(timedelta(hours=8))
MAX_AGE_DAYS = 7


def _ts():
    return datetime.now(CST).strftime("%Y%m%dT%H%M%SZ")


def _run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return -1, str(e)


def _dsn():
    try:
        for line in open(os.path.join(BASE, "state", ".cq-dsn")):
            line = line.strip()
            if line.startswith("CQ_DSN="):
                return line[7:].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def main():
    try:
        reg = json.load(open(REG, encoding="utf-8"))
    except Exception as e:
        print("FAIL: registry unreadable: %s" % e)
        return 2
    fails, warns, oks = [], [], []

    try:
        verified_at = datetime.fromisoformat(reg.get("verified_at", ""))
    except Exception:
        verified_at = None
    if verified_at is None:
        fails.append("verified_at missing/unparseable")
    elif (datetime.now(CST) - verified_at).days > MAX_AGE_DAYS:
        fails.append("EXPIRED: verified_at %s is > %d days old" % (reg["verified_at"], MAX_AGE_DAYS))
    else:
        oks.append("verified_at %s fresh" % reg["verified_at"])

    for d in reg.get("directories", []):
        p = d.get("path", "")
        full = p if os.path.isabs(p) else os.path.join(BASE, p)
        if os.path.exists(full):
            oks.append("dir %s" % p)
        else:
            fails.append("dir missing: %s" % p)

    for t in reg.get("tools", []):
        p = os.path.join(BASE, t.get("path", ""))
        if os.path.exists(p):
            oks.append("tool %s" % t.get("id"))
        else:
            fails.append("tool missing: %s (%s)" % (t.get("id"), t.get("path")))

    for c in reg.get("canonical_paths", []):
        p = c.get("canonical", "")
        full = p if p.startswith("/") else os.path.join(BASE, p)
        if p.startswith("PG "):
            continue
        if "*" in full:
            if glob.glob(full):
                oks.append("canonical %s" % c.get("artifact"))
            else:
                fails.append("canonical missing: %s (%s)" % (c.get("artifact"), p))
            continue
        if os.path.exists(full):
            oks.append("canonical %s" % c.get("artifact"))
        else:
            fails.append("canonical missing: %s (%s)" % (c.get("artifact"), p))

    rc, crontab = _run(["crontab", "-l"])
    if rc != 0:
        fails.append("cannot read live crontab")
        crontab = ""
    for c in reg.get("crons", []):
        cmd = c.get("command", "")
        if cmd and cmd in crontab:
            oks.append("cron %s" % c.get("id"))
        else:
            fails.append("cron missing from live crontab: %s (%s)" % (c.get("id"), cmd))

    rc, out = _run(["pgrep", "-f", "beszel serve"])
    if rc == 0 and out.strip():
        oks.append("service beszel")
    else:
        fails.append("service beszel: no 'beszel serve' process")
    rc, out = _run(["docker", "ps", "--format", "{{.Names}}"])
    if "openclaw-official-openclaw-gateway-1" in out:
        oks.append("service gateway")
    else:
        fails.append("service gateway: container not in docker ps")
    dsn = _dsn()
    if not dsn:
        fails.append("service postgres: no DSN in state/.cq-dsn")
    else:
        rc, out = _run(["psql", dsn, "-t", "-A", "-c", "SELECT 1;"])
        if rc == 0 and out.strip() == "1":
            oks.append("service postgres")
        else:
            fails.append("service postgres: psql SELECT 1 failed")

    canon_v = os.path.join(BASE, "ceo-seat", "state", "butler-verdicts")
    legacy_v = os.path.join(BASE, "state", "butler-verdicts")
    try:
        c_files = [os.path.join(canon_v, f) for f in os.listdir(canon_v) if f.endswith(".json")]
        l_files = [os.path.join(legacy_v, f) for f in os.listdir(legacy_v) if f.endswith(".json")]
        if c_files and l_files:
            newest_c = max(os.path.getmtime(f) for f in c_files)
            newest_l = max(os.path.getmtime(f) for f in l_files)
            if newest_l > newest_c:
                warns.append("verdict sanity: legacy dir has a newer verdict than canonical (writes may go wrong way)")
            else:
                oks.append("verdict sanity: canonical is newest")
    except OSError as e:
        warns.append("verdict sanity skipped: %s" % e)

    fp = hashlib.sha256("\n".join(sorted(fails + warns)).encode()).hexdigest()[:12]
    last_fp = ""
    try:
        with open(RECEIPT, encoding="utf-8") as f:
            for line in f:
                if "fingerprint=" in line:
                    last_fp = line.split("fingerprint=")[-1].strip().split()[0]
    except OSError:
        pass
    os.makedirs(os.path.dirname(RECEIPT), exist_ok=True)
    with open(RECEIPT, "a", encoding="utf-8") as f:
        f.write("%s ok=%d fail=%d warn=%d fingerprint=%s\n" % (_ts(), len(oks), len(fails), len(warns), fp))
    if (fails or warns) and fp != last_fp:
        os.makedirs(OUTBOX, exist_ok=True)
        with open(os.path.join(OUTBOX, "hub-registry-drift-%s.md" % _ts()), "w", encoding="utf-8") as f:
            f.write("# Hub registry drift %s\n\n" % _ts())
            for x in fails:
                f.write("- FAIL: %s\n" % x)
            for x in warns:
                f.write("- WARN: %s\n" % x)
            f.write("\nRegistry: state/hub-registry.json. Re-verify and update, or fix the hub.\n")

    for x in fails:
        print("FAIL: %s" % x)
    for x in warns:
        print("WARN: %s" % x)
    print("RESULT: ok=%d fail=%d warn=%d fingerprint=%s" % (len(oks), len(fails), len(warns), fp))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
