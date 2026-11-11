#!/usr/bin/env python3
"""Watchdog (deterministic, no LLM, no gateway) — lands state/watchdog-draft-20261106.md §6 item 1.

Implements draft §2 triggers against central_tasks / central_events:
  2.1 stale-SLA: non-terminal task (READY/DISPATCHING/CLAIMED/RUNNING/VERIFYING/
      RETRY_WAIT) whose updated_at is older than its class SLA.
      SLA (payload->>'type'): code 60m, llm_doc 30m, patrol/timer/canary 10m,
      anything else 60m (draft default). worker_class is 'main' for all rows,
      so payload type is the class discriminator.
  2.2 C2 failure-rate: in the last 30m, tasks leaving VERIFYING that failed
      (→ RETRY_WAIT / QUARANTINED) are >=50% AND sample >=4.
      LIMITATION (2026-11-07 verified): central_events is EMPTY, so no
      VERIFYING-entry history exists. Proxy used: tasks whose updated_at is in
      the last 30m with a terminal-ish outcome status (DONE vs RETRY_WAIT /
      QUARANTINED). The proxy is labelled as such in output; when
      central_events starts receiving rows, switch denominator to it.
  2.3 DONE stall: 0 DONE in the last 60m AND (READY + RETRY_WAIT) > 0.

Behaviour (draft §3/§4): NEVER mutates task status, never calls LLM/gateway.
Default is --dry-run: read-only, prints JSON findings, exit 0 (no trigger)
or 2 (trigger fired). --live additionally writes one outbox file per trigger
(state/outbox/watchdog-<trigger>-<key>.md) and appends one full-schema
entry per trigger to state/decision-queue.json pending[] (id/type/task_id/
summary/options, tagged needs_chairman + category by the four-category
gate -- 2026-11-07 fix, the first version wrote bare untagged dicts);
a trigger already filed and still pending is reported as deduped, not
re-filed. Rollback: stop the cron; outbox files are left as a trail
(draft §6 item 5).

DB access reuses the retrywait-scan-20261105.py pattern: DSN from
state/.cq-dsn, read-only SELECTs via the psql CLI. No password is ever
printed; PGPASSWORD is passed via environment only.
"""
import argparse
import datetime
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

BASE = Path("/home/ubuntu/ai-team")
DSN_PATH = BASE / "state" / ".cq-dsn"
OUTBOX = BASE / "state" / "outbox"
DECISION_QUEUE = BASE / "state" / "decision-queue.json"

SLA_MINUTES = {"code": 60, "llm_doc": 30, "patrol": 10, "timer": 10,
               "canary": 10}
DEFAULT_SLA_MINUTES = 60
FAIL_RATE_THRESHOLD = 0.5
FAIL_RATE_MIN_SAMPLE = 4
DONE_STALL_WINDOW_MINUTES = 60

NON_TERMINAL = ("READY", "DISPATCHING", "CLAIMED", "RUNNING", "VERIFYING",
                "RETRY_WAIT")


def sla_minutes(task_type):
    """SLA minutes for a task class (draft §2.1). Unknown class -> default."""
    return SLA_MINUTES.get((task_type or "").strip().lower(),
                           DEFAULT_SLA_MINUTES)


def parse_ts(s):
    if not s:
        return None
    s = str(s).strip()
    s = re.sub(r"\.\d+", "", s, count=1)
    try:
        t = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return t.astimezone(datetime.timezone.utc)


def check_stale(rows, now):
    """rows: dicts with task_id, task_type, status, updated_at (datetime),
    owner. Returns list of stale findings (one per task)."""
    out = []
    for r in rows:
        if r.get("status") not in NON_TERMINAL:
            continue
        upd = r.get("updated_at")
        if upd is None:
            continue
        sla = sla_minutes(r.get("task_type"))
        stuck = (now - upd).total_seconds() / 60.0
        if stuck >= sla:
            out.append({"trigger": "watchdog-stale", "task_id": r["task_id"],
                        "task_type": r.get("task_type"), "status": r["status"],
                        "sla_minutes": sla, "stuck_minutes": round(stuck, 1),
                        "last_change": upd.isoformat(),
                        "owner": r.get("owner")})
    return out


def check_failure_rate(failed, total):
    """§2.2: failed>=50% and sample>=4. Returns dict with fired flag."""
    rate = (failed / total) if total else 0.0
    fired = total >= FAIL_RATE_MIN_SAMPLE and rate >= FAIL_RATE_THRESHOLD
    return {"trigger": "watchdog-degrade", "failed": failed, "sample": total,
            "rate": round(rate, 3), "threshold": FAIL_RATE_THRESHOLD,
            "min_sample": FAIL_RATE_MIN_SAMPLE, "fired": fired}


def check_done_stall(done_last_60m, ready_plus_retrywait):
    """§2.3: 0 DONE in 60m while work is still pending. Returns dict."""
    fired = done_last_60m == 0 and ready_plus_retrywait > 0
    return {"trigger": "watchdog-no-done", "done_last_60m": done_last_60m,
            "ready_plus_retrywait": ready_plus_retrywait, "fired": fired}


def parse_dsn(dsn):
    s = dsn.strip()
    if s.startswith("CQ_DSN="):
        s = s[len("CQ_DSN="):]
    s = s.strip().strip('"')
    out = {}
    for key in ("host", "port", "dbname", "user", "password"):
        m = re.search(key + r"=(\S+)", s)
        if not m:
            raise ValueError("Invalid DSN format: missing " + key)
        out[key] = m.group(1)
    return out


def psql_query(dsn_data, sql):
    env = os.environ.copy()
    env["PGPASSWORD"] = dsn_data["password"]
    cmd = ["psql", "-h", dsn_data["host"], "-p", dsn_data["port"],
           "-d", dsn_data["dbname"], "-U", dsn_data["user"],
           "-v", "ON_ERROR_STOP=1", "-At", "-F", "|", "-c", sql]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, env=env)
    stdout, stderr = p.communicate()
    if p.returncode != 0:
        raise RuntimeError("psql query failed: " + stderr.decode()[:500])
    return [ln for ln in stdout.decode().strip().split("\n") if ln]


def scan_live(dsn_data):
    """Read-only scan. Returns findings dict (all three triggers)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    rows = psql_query(
        dsn_data,
        "SELECT task_id, coalesce(payload->>'type',''), status, updated_at,"
        " coalesce(owner,'') FROM central_tasks"
        " WHERE status IN ('READY','DISPATCHING','CLAIMED','RUNNING',"
        "'VERIFYING','RETRY_WAIT') ORDER BY updated_at")
    parsed = []
    for ln in rows:
        parts = ln.split("|")
        if len(parts) < 5:
            continue
        parsed.append({"task_id": parts[0], "task_type": parts[1],
                       "status": parts[2], "updated_at": parse_ts(parts[3]),
                       "owner": parts[4]})
    stale = check_stale(parsed, now)
    # §2.2 proxy (central_events empty, see module docstring): outcomes
    # decided in the last 30m.
    out30 = psql_query(
        dsn_data,
        "SELECT status, count(*) FROM central_tasks"
        " WHERE updated_at > now() - interval '30 minutes'"
        " AND status IN ('DONE','RETRY_WAIT','QUARANTINED')"
        " GROUP BY status")
    counts = {}
    for ln in out30:
        try:
            k, v = ln.split("|")
            counts[k] = int(v)
        except ValueError:
            continue
    failed = counts.get("RETRY_WAIT", 0) + counts.get("QUARANTINED", 0)
    total = failed + counts.get("DONE", 0)
    fail = check_failure_rate(failed, total)
    fail["proxy"] = "central_events empty; outcome proxy from central_tasks"
    # §2.3
    done60 = psql_query(
        dsn_data,
        "SELECT count(*) FROM central_tasks WHERE status='DONE'"
        " AND updated_at > now() - interval '60 minutes'")
    pending = psql_query(
        dsn_data,
        "SELECT count(*) FROM central_tasks"
        " WHERE status IN ('READY','RETRY_WAIT')")
    stall = check_done_stall(int(done60[0]) if done60 else 0,
                             int(pending[0]) if pending else 0)
    return {"checked_at_utc": now.isoformat(), "stale": stale,
            "failure_rate": fail, "done_stall": stall}


def dedup_key(trigger, key):
    return "watchdog:%s:%s" % (trigger, key)


def _gate(item):
    """Four-category gate (same rule as notify.py push): returns
    (needs_chairman, reason). Fail closed: if the gate cannot run, the
    entry is tagged as needing the chairman."""
    try:
        spec = importlib.util.spec_from_file_location(
            "four_category_gate",
            str(BASE / "scripts" / "four-category-gate.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.needs_chairman(item)
    except Exception as e:
        return (True, "gate-error:" + str(e)[:80])


def _next_id(q, now):
    """Next dec-YYYYMMDD-NNN id, scanning all three queue lists (same
    sequence space as notify.py push, so ids never collide)."""
    prefix = "dec-" + now.strftime("%Y%m%d") + "-"
    seq = 0
    for lst in (q.get("pending", []), q.get("resolved", []),
                q.get("auto_decisions", [])):
        for e in lst:
            i = str(e.get("id", ""))
            if i.startswith(prefix):
                try:
                    n = int(i[len(prefix):])
                except ValueError:
                    continue
                if n > seq:
                    seq = n
    return "%s%03d" % (prefix, seq + 1)


def file_live(findings):
    """Write outbox files + decision-queue entries. Returns filed list.
    Queue entries are FULL schema entries (id/type/task_id/summary/
    options/deadline_hint) tagged by the four-category gate
    (needs_chairman + category) -- 2026-11-07 fix: the first version
    appended bare {dedup_key, category, key, detail} dicts straight to
    pending[], bypassing the gate and breaking every consumer that
    reads id/task_id/summary (notify close, eight-layer auto-close) and
    making health_3q Q3 count them as chairman-needed (fail closed).
    Entries still go to pending[] (draft §2 intent: visible to the
    butler); the tag records the true verdict so Q3 and the push cron
    can tell chairman-needed from self-decidable."""
    now = datetime.datetime.now(datetime.timezone.utc)
    filed, deduped = [], []
    events = []
    for s in findings["stale"]:
        events.append(("watchdog-stale", s["task_id"], s))
    if findings["failure_rate"]["fired"]:
        events.append(("watchdog-degrade", "c2-pipeline",
                       findings["failure_rate"]))
    if findings["done_stall"]["fired"]:
        events.append(("watchdog-no-done", "no-done-60m",
                       findings["done_stall"]))
    OUTBOX.mkdir(parents=True, exist_ok=True)
    try:
        q = json.loads(DECISION_QUEUE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        q = {"pending": [], "resolved": [], "auto_decisions": []}
    for trigger, key, detail in events:
        dk = dedup_key(trigger, key)
        if any(i.get("dedup_key") == dk for i in q.get("pending", [])):
            deduped.append(dk)
            continue
        fname = OUTBOX / ("watchdog-%s-%s-%s.md"
                          % (trigger, key, now.strftime("%Y%m%dT%H%M%SZ")))
        fname.write_text(
            "# Watchdog %s\n\n- trigger: %s\n- key: %s\n- at (UTC): %s\n"
            "- detail: %s\n" % (trigger, trigger, key, now.isoformat(),
                                json.dumps(detail, ensure_ascii=False)),
            encoding="utf-8")
        if trigger == "watchdog-stale":
            summary = ("watchdog-stale: task %s (%s) status %s stuck %.1f min "
                       "(SLA %d min), last change %s, owner %s"
                       % (detail.get("task_id"), detail.get("task_type"),
                          detail.get("status"), detail.get("stuck_minutes", 0),
                          detail.get("sla_minutes", 0),
                          detail.get("last_change"), detail.get("owner")))
        elif trigger == "watchdog-degrade":
            summary = ("watchdog-degrade: C2 failure rate %.1f%% (%d/%d) in "
                       "the last 30 min (threshold 50%%, min sample 4)"
                       % (detail.get("rate", 0) * 100, detail.get("failed", 0),
                          detail.get("sample", 0)))
        else:
            summary = ("watchdog-no-done: 0 DONE in the last 60 min while "
                       "%d tasks are READY/RETRY_WAIT"
                       % detail.get("ready_plus_retrywait", 0))
        entry = {
            "id": _next_id(q, now),
            "created_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
            "type": trigger,
            "task_id": str(key),
            "summary": summary,
            "five_step_path": "",
            "options": [],
            "deadline_hint": "24 小时内不决定，会自动降级到 B",
            "dedup_key": dk,
            "outbox": str(fname),
        }
        need, reason = _gate(entry)
        entry["needs_chairman"] = need
        entry["category"] = reason
        q.setdefault("pending", []).append(entry)
        filed.append(dk)
    DECISION_QUEUE.write_text(json.dumps(q, indent=1, ensure_ascii=False),
                              encoding="utf-8")
    return filed, deduped


def main(argv=None):
    ap = argparse.ArgumentParser(description="Deterministic watchdog "
                                 "(draft §2.1/2.2/2.3). Default: dry-run.")
    ap.add_argument("--live", action="store_true",
                    help="write outbox + decision-queue (default dry-run)")
    ap.add_argument("--dsn-file", default=str(DSN_PATH))
    args = ap.parse_args(argv)
    try:
        dsn_data = parse_dsn(Path(args.dsn_file).read_text())
        findings = scan_live(dsn_data)
    except (OSError, ValueError, RuntimeError) as e:
        print(json.dumps({"error": str(e)[:300]}, ensure_ascii=False),
              file=sys.stderr)
        return 1
    fired = bool(findings["stale"]) or findings["failure_rate"]["fired"] \
        or findings["done_stall"]["fired"]
    if args.live and fired:
        filed, deduped = file_live(findings)
        findings["filed"] = filed
        findings["deduped"] = deduped
    else:
        findings["filed"] = []
        findings["deduped"] = []
        findings["mode"] = "dry-run"
    print(json.dumps(findings, indent=1, ensure_ascii=False))
    return 2 if fired else 0


if __name__ == "__main__":
    sys.exit(main())
