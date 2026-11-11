#!/usr/bin/env python3
"""orphan-disposition-20261110.py -- cutover51 W6.5 (D-2).

Merges state/watchdog-registry.json (selected 21) with
state/watchdog-registry-20261107.json (snapshot 42, orphans 26) and disposes
each orphan by the D-2 rules, checked against LIVE state (not the 11-07
snapshot):
  retain  -- object exists AND (output/alert in last 30d) AND (downstream
             consumer or responsible seat / now wired in cron or called)
  archive -- object exists but no output in 30d, OR duplicate of a selected
             entry, OR unclear owner; cron stopped (none running), code kept.
  (delete is never used: even vanished objects archive first, listed for
   the housekeeper's review.)
Outputs: state/orphans-disposition.csv + merged registry JSON
(state/watchdog-registry-merged-20261110.json, generated artifact).
"""
import csv
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

BASE = "/home/ubuntu/ai-team"
SCRIPTS = os.path.join(BASE, "scripts")
STATE = os.path.join(BASE, "state")
LOGS = [os.path.join(BASE, "state", "logs"), os.path.join(BASE, "logs")]

SELECTED = json.load(open(os.path.join(STATE, "watchdog-registry.json")))["entries"]
SNAP = json.load(open(os.path.join(STATE, "watchdog-registry-20261107.json")))
ORPHANS = SNAP["orphans"]
SELECTED_NAMES = {e["name"] for e in SELECTED}
SNAP_NAMES = [w.get("name") or w.get("script") for w in SNAP["watchdogs"]]

try:
    CRONTAB = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=30).stdout
except Exception:
    CRONTAB = ""


def find_file(name):
    for base in (SCRIPTS, os.path.join(STATE, "slim-20261102", "scripts"), os.path.join(BASE, "state", "l3-v2")):
        p = os.path.join(base, name)
        if os.path.isfile(p):
            return p
    hits = glob.glob(os.path.join(BASE, "**", name), recursive=True)
    hits = [h for h in hits if "__pycache__" not in h and ".bak" not in h]
    return hits[0] if hits else None


def core(name):
    """Distinctive core token: strip date suffix and extension.
    pipeline-canary-20261106.py -> pipeline-canary; health_3q-20261104.py ->
    health_3q; cf-neuron-guard-20261107.py -> cf-neuron-guard."""
    stem = name.rsplit(".", 1)[0]
    stem = re.sub(r"-\d{8}$", "", stem)
    return stem


# Known output files that do not carry the watchdog's own name.
OUTPUT_MAP = {
    "canary_b_tracker-20261104.py": ["state/gateway-pool.json"],
    "gateway_canary_tier2-20261104.py": ["state/gateway-pool.json"],
    "gateway_canary-20261104.py": ["state/gateway-canary-20261104.jsonl"],
    "health_3q-20261104.py": ["state/logs/health_3q_reporter.log", "state/logs/health-3q.log"],
    "health_3q_supplement-20261104.py": ["state/logs/health_3q_reporter.log"],
    "pipeline-canary-20261106.py": ["state/logs/pipeline-canary.log"],
}


def last_output(name):
    """Newest mtime (UTC iso) of any output file for this watchdog: files
    under state/ or logs/ whose name contains the core token, plus the
    explicit OUTPUT_MAP entries."""
    import re as _re  # local import to keep top tidy
    c = core(name)
    cands = []
    for rel in OUTPUT_MAP.get(name, []):
        cands.append(os.path.join(BASE, rel))
    for root in (STATE, os.path.join(BASE, "logs")):
        for dirpath, _dirs, files in os.walk(root):
            if "__pycache__" in dirpath or "backups" in dirpath:
                continue
            for f in files:
                if c in f or name in f:
                    cands.append(os.path.join(dirpath, f))
    best = None
    for p in set(cands):
        try:
            m = datetime.fromtimestamp(os.path.getmtime(p), tz=timezone.utc)
            if best is None or m > best[0]:
                best = (m, os.path.relpath(p, BASE))
        except Exception:
            pass
    return best


def callers(name):
    stem = name.rsplit(".", 1)[0]
    hits = []
    try:
        r = subprocess.run(["grep", "-rl", "--include=*.py", "--include=*.sh",
                            "--exclude-dir=__pycache__", stem, SCRIPTS],
                           capture_output=True, text=True, timeout=60)
        for f in r.stdout.splitlines():
            if os.path.basename(f) != name and os.path.basename(f) != "orphan-disposition-20261110.py":
                hits.append(os.path.basename(f))
    except Exception:
        pass
    return sorted(set(hits))


def main():
    rows = []
    now = datetime.now(timezone.utc)
    for name in ORPHANS:
        path = find_file(name)
        alive = path is not None
        in_cron = core(name) in CRONTAB or name in CRONTAB
        cls = callers(name) if alive else []
        lo = last_output(name) if alive else None
        lo_str = lo[0].strftime("%Y-%m-%dT%H:%M:%SZ") if lo else "none"
        age_d = (now - lo[0]).days if lo else 9999
        # duplicate of a selected entry (same stem, different file)?
        dup = None
        for s in SELECTED_NAMES:
            if s != name and s.rsplit(".", 1)[0] == name.rsplit(".", 1)[0]:
                dup = s
        if not alive:
            verdict, basis = "archive", "object vanished (file not found); archive first, list for housekeeper review"
        elif name in SELECTED_NAMES:
            verdict, basis = "retain", "already in selected registry; kept as registered entry"
        elif dup:
            verdict, basis = "archive", "duplicate of selected %s" % dup
        elif in_cron and age_d <= 30:
            verdict, basis = "retain", "in crontab + output %s (%dd ago)" % (lo_str, age_d)
        elif cls and age_d <= 30:
            verdict, basis = "retain", "called by %s + output %s (%dd ago)" % (",".join(cls[:2]), lo_str, age_d)
        elif age_d <= 30 and (in_cron or cls):
            verdict, basis = "retain", "recent output %s (%dd ago), consumer %s" % (lo_str, age_d, ",".join(cls[:2]) or "cron")
        else:
            verdict, basis = "archive", "no cron/caller wiring and no output in 30d (last: %s); code retained, cron stopped" % lo_str
        rows.append({"id": name, "verdict": verdict,
                     "basis": "alive=%s; last_output=%s; consumer=%s; %s" % (
                         alive, lo_str, ",".join(cls) if cls else ("crontab" if in_cron else "none"), basis)})
    merged = {"generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "source": "watchdog-registry.json (21 selected) + watchdog-registry-20261107.json (42 snapshot)",
              "selected": sorted(SELECTED_NAMES),
              "snapshot": sorted(set(SNAP_NAMES)),
              "orphan_disposition": rows}
    with open(os.path.join(STATE, "orphans-disposition.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "verdict", "basis"])
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(STATE, "watchdog-registry-merged-20261110.json"), "w") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)
    n_ret = sum(1 for r in rows if r["verdict"] == "retain")
    n_arc = len(rows) - n_ret
    print("orphans=%d retain=%d archive=%d delete=0" % (len(rows), n_ret, n_arc))
    for r in rows:
        print(" %s %s -- %s" % (r["verdict"].upper(), r["id"], r["basis"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
