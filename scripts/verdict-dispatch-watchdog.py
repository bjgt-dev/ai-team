#!/usr/bin/env python3
"""Watchdog: flag butler verdicts (pass/conditional pass) older than 15 min
with no matching task in central_tasks. Writes an alert to state/outbox/
once per verdict (idempotent via a seen file). No network, no LLM, no spend."""
import json, os, re, subprocess, sys, time
STATE = "/home/ubuntu/ai-team/state"
VERDICT_DIR = os.path.join(STATE, "butler-verdicts")
CEO_VERDICT_DIR = "/home/ubuntu/ai-team/ceo-seat/state/butler-verdicts"
OUTBOX = os.path.join(STATE, "outbox")
SEEN = os.path.join(STATE, "verdict-watchdog-seen.json")
DSN_FILE = os.path.join(STATE, ".cq-dsn")
THRESHOLD_SEC = 15 * 60

def normalize_task(s):
    """Match normalization: 'iteration' -> 'iter' so kids-iter4 matches
    kids-iteration4; '-p0' stripped so a dispatched '<task>-p0-<date>'
    title still contains the verdict task name; characters outside
    [A-Za-z0-9_-] collapsed to '-' (same rule verdict-auto-dispatch
    applies when it builds the task_id, so a task with spaces still
    matches its dispatched title)."""
    s = (s or "").replace("iteration", "iter")
    s = s.replace("-p0", "")
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", s)
    return s

# cq-scan 2026-11-07 (L3): only a LIVE task counts as "dispatched".
# A DONE or QUARANTINED row with a matching title is history, not work
# in flight. Before this fix the match was status-blind, so the
# kb-retrieval-20261106 conditional-pass verdict (whose only queue row
# is QUARANTINED 3881d270) was treated as already dispatched and its
# re-entry task was never created -- while the watchdog log kept
# printing "OK: no undispatched verdict". The verdict itself demands a
# NEW task number for re-entry, which is exactly what auto-dispatch
# builds once the match stops counting terminal rows.
LIVE_STATUSES = {"READY", "DISPATCHING", "CLAIMED", "RUNNING", "VERIFYING", "RETRY_WAIT"}

def is_dispatched(task, title_status_pairs):
    """True when a LIVE queue row's title contains the (normalized)
    task name. title_status_pairs is an iterable of (title, status).
    A plain set of titles is still accepted (status unknown -> treated
    as live, the old behaviour) so older callers keep working."""
    nt = normalize_task(task)
    if not nt:
        return False
    for item in title_status_pairs:
        if isinstance(item, (list, tuple)):
            title, status = item[0], item[1]
        else:
            title, status = item, None
        if status is not None and status not in LIVE_STATUSES:
            continue
        if nt in normalize_task(title):
            return True
    return False

def get_dsn():
    for line in open(DSN_FILE):
        line = line.strip()
        if line.startswith("CQ_DSN="):
            return line[7:].strip().strip('"').strip("'")
    return ""

def queue_titles(dsn):
    """Return a list of (title, status) for every central_tasks row.
    Status is needed by is_dispatched (L3); callers that only need
    titles can take item[0]."""
    out = subprocess.run(
        ["psql", dsn, "-t", "-A", "-F", "|", "-c",
         "SELECT title, status FROM central_tasks;"],
        capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise RuntimeError("psql failed: " + out.stderr[:200])
    pairs = []
    for line in out.stdout.splitlines():
        if not line.strip():
            continue
        if "|" in line:
            title, status = line.split("|", 1)
            pairs.append((title.strip(), status.strip()))
        else:
            pairs.append((line.strip(), None))
    return pairs

def main():
    dsn = get_dsn()
    if not dsn:
        print("no DSN, fail closed")
        return 1
    try:
        titles = queue_titles(dsn)
    except Exception as e:
        print("queue check failed:", e)
        return 1
    seen = {}
    if os.path.exists(SEEN):
        try:
            seen = json.load(open(SEEN))
        except Exception:
            seen = {}
    now = time.time()
    alerts = []
    verdict_path_by_name = {}
    verdict_files = []
    for _d in (CEO_VERDICT_DIR, VERDICT_DIR):
        if not os.path.isdir(_d):
            continue
        for _n in sorted(os.listdir(_d)):
            if not _n.endswith(".json") or _n in verdict_path_by_name:
                continue
            verdict_path_by_name[_n] = os.path.join(_d, _n)
            verdict_files.append(os.path.join(_d, _n))
    for path in verdict_files:
        name = os.path.basename(path)
        try:
            v = json.load(open(path))
        except Exception:
            continue
        verdict = v.get("verdict", "")
        if verdict not in ("通过", "有条件通过"):
            continue
        task = v.get("task", "") or name[:-5]
        matched = is_dispatched(task, titles)
        if matched:
            continue
        age = now - os.path.getmtime(path)
        if age < THRESHOLD_SEC:
            continue
        # Seen key is the NORMALIZED task name, so the two spellings of
        # the same work (kids-iter4 / kids-iteration4) share one entry;
        # raw keys from older runs are still honoured for one cycle.
        seen_key = normalize_task(task)
        if seen.get(task) or seen.get(seen_key):
            continue
        alerts.append((task, name, int(age // 60), verdict))
        seen[seen_key] = int(now)
    if alerts:
        os.makedirs(OUTBOX, exist_ok=True)
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        lines = ["# Verdict dispatch watchdog " + ts, ""]
        for task, name, mins, verdict in alerts:
            vpath = verdict_path_by_name[name]
            r = subprocess.run(
                ["python3", "/home/ubuntu/ai-team/scripts/verdict-auto-dispatch.py", vpath],
                capture_output=True, text=True, timeout=120)
            out = (r.stdout or "").strip()
            if r.returncode == 0:
                lines.append("- %s (%s): verdict '%s' %d min ago -> AUTO-DISPATCHED: %s" % (task, name, verdict, mins, out))
                print("AUTO-DISPATCHED %s: %s" % (task, out))
                # 2026-11-07 fix: titles were snapshotted once per run, so
                # two spelling variants of the same work (kids-iter4 +
                # kids-iteration4) both dispatched in one run and created
                # duplicate tasks. Refresh after every success so the next
                # verdict re-matches against the just-created title.
                try:
                    titles = queue_titles(dsn)
                except Exception as e:
                    print("title refresh failed (keeping stale set): %s" % e)
            else:
                err = ((r.stdout or "") + (r.stderr or "")).strip()[:300]
                lines.append("- %s (%s): verdict '%s' %d min ago, AUTO-DISPATCH FAILED: %s" % (task, name, verdict, mins, err))
                print("AUTO-DISPATCH FAILED %s: %s" % (task, err))
        lines.append("")
        lines.append("Undispatched verdicts are auto-dispatched when possible; failures need a human to dispatch or record why not.")
        with open(os.path.join(OUTBOX, "verdict-dispatch-watch-%s.md" % ts), "w") as f:
            f.write("\n".join(lines))
    else:
        print("OK: no undispatched verdict older than 15 min")
    json.dump(seen, open(SEEN, "w"))
    return 0

if __name__ == "__main__":
    sys.exit(main())
