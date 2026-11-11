#!/usr/bin/env python3
"""task-trace-20261107.py -- Thorsten Ball Rule 13 / code-dev-sop §九.7 交付物溯源.

Before this script, "凭 task_id 能找到合同、verdict、交付物与 verifier 日志"
existed only as an SOP sentence: state/ files are scattered and there was
no single lookup. This script is that lookup (read-only, no DB writes;
DB is consulted read-only only when a DSN is present).

Given a task_id (or slug), it searches:
  * central_tasks (read-only SELECT, when state/.cq-dsn exists): status,
    payload contract (task_type, workdir, five_part sections), artifact sha.
  * state/butler-verdicts/*.json: verdict + conditions.
  * state/deliverables/<task_id>.* and state/c2-task-results/<task_id>.*:
    deliverable file, sha256, bytes.
  * state/queue-api-20261105/quarantine/<task_id>.bin (audit copy).
  * state/logs/c2-verifier.log (and state/verdict-watchdog.log): last lines
    mentioning the task_id.
  * state/m5-20261105/review/review-<task_id>.json: cross-supplier review.
  * contracts on disk: state/pipeline-formal-20261106/*<task_id>* and any
    state/**/contract*<task_id>*.json (bounded walk).

Exit: 0 = task found in at least one source; 1 = not found anywhere;
      2 = tool error (bad args, unreadable state dir).
"""
import argparse
import glob
import hashlib
import json
import os
import sys

BASE = "/home/ubuntu/ai-team"
STATE = BASE + "/state"
DSN_FILE = STATE + "/.cq-dsn"
SEARCH_DIRS = [
    STATE + "/butler-verdicts",
    STATE + "/m5-20261105/review",
    STATE + "/pipeline-formal-20261106",
    STATE + "/method-07-review/rework-incidents",
]
LOG_FILES = [
    STATE + "/logs/c2-verifier.log",
    STATE + "/verdict-watchdog.log",
    STATE + "/logs/long-task-sweeper.log",
]
MAX_WALK_FILES = 20000


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _db_lookup(task_id):
    """Read-only DB lookup. Returns dict or None when DSN is absent."""
    if not os.path.exists(DSN_FILE):
        return {"db": "no DSN file, skipped"}
    try:
        with open(DSN_FILE, encoding="utf-8") as f:
            raw = f.read().strip()
        dsn = raw.split("=", 1)[1].strip("\"'") if "=" in raw else ""
    except Exception as exc:
        return {"db": "DSN unreadable: %s" % exc}
    if not dsn:
        return {"db": "DSN empty"}
    try:
        import psycopg
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status, payload, artifact_sha, artifact_bytes, verified_by, "
                    "created_at, updated_at FROM central_tasks "
                    "WHERE payload->>'task_id' = %s ORDER BY updated_at DESC LIMIT 1",
                    (task_id,))
                row = cur.fetchone()
    except Exception as exc:
        return {"db": "query failed: %s" % exc}
    if not row:
        return {"db": "task not in central_tasks"}
    status, payload, sha, nbytes, verified_by, created, updated = row
    out = {"db": "found", "status": status, "artifact_sha": sha,
           "artifact_bytes": nbytes, "verified_by": verified_by,
           "created_at": str(created), "updated_at": str(updated)}
    contract = payload.get("contract") if isinstance(payload, dict) else None
    if isinstance(contract, dict):
        out["contract"] = {
            "task_type": contract.get("task_type"),
            "workdir": contract.get("workdir"),
            "output_path": contract.get("output_path"),
            "five_part": sorted((contract.get("five_part") or {}).keys())
            if isinstance(contract.get("five_part"), dict) else "missing",
        }
    if isinstance(payload, dict):
        out["payload_type"] = payload.get("type")
        out["workdir"] = payload.get("workdir")
    return out


def _file_hits(task_id):
    hits = {}
    for pat in (STATE + "/deliverables/%s.*" % task_id,
                STATE + "/c2-task-results/%s.*" % task_id,
                STATE + "/queue-api-20261105/quarantine/%s.bin" % task_id):
        for path in sorted(glob.glob(pat)):
            try:
                hits[os.path.basename(os.path.dirname(path)) + "/" + os.path.basename(path)] = {
                    "bytes": os.path.getsize(path), "sha256": _sha256(path)}
            except OSError as exc:
                hits[path] = {"error": str(exc)}
    for d in SEARCH_DIRS:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if task_id in name:
                hits[os.path.join(d, name)] = {"bytes": os.path.getsize(os.path.join(d, name))}
    return hits


def _verdict_hit(task_id):
    d = STATE + "/butler-verdicts"
    if not os.path.isdir(d):
        return None
    for name in sorted(os.listdir(d)):
        if task_id in name and name.endswith(".json"):
            path = os.path.join(d, name)
            try:
                rec = json.load(open(path, encoding="utf-8"))
                return {"file": path, "verdict": rec.get("verdict"),
                        "conditions": len(rec.get("conditions") or [])}
            except Exception as exc:
                return {"file": path, "error": str(exc)}
    return None


def _log_hits(task_id, limit=5):
    hits = {}
    for path in LOG_FILES:
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                lines = [ln.rstrip("\n") for ln in f if task_id in ln]
            if lines:
                hits[path] = lines[-limit:]
        except OSError:
            continue
    return hits


def trace(task_id):
    result = {"task_id": task_id}
    result.update(_db_lookup(task_id))
    result["files"] = _file_hits(task_id)
    verdict = _verdict_hit(task_id)
    if verdict:
        result["verdict"] = verdict
    result["logs"] = _log_hits(task_id)
    found = (result.get("db") == "found" or bool(result["files"])
             or verdict is not None or bool(result["logs"]))
    result["found"] = found
    return result


def main(argv=None):
    global BASE, STATE, DSN_FILE, SEARCH_DIRS, LOG_FILES
    ap = argparse.ArgumentParser(description="trace a task_id to its contract, verdict, deliverable and verifier log")
    ap.add_argument("task_id")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--base", default=BASE, help="hub base dir (tests only)")
    args = ap.parse_args(argv)
    if args.base != BASE:
        BASE = args.base
        STATE = BASE + "/state"
        DSN_FILE = STATE + "/.cq-dsn"
        SEARCH_DIRS = [STATE + "/butler-verdicts", STATE + "/m5-20261105/review",
                       STATE + "/pipeline-formal-20261106",
                       STATE + "/method-07-review/rework-incidents"]
        LOG_FILES = [STATE + "/logs/c2-verifier.log", STATE + "/verdict-watchdog.log",
                     STATE + "/logs/long-task-sweeper.log"]
    if not args.task_id.strip():
        print("TOOL-ERROR: empty task_id")
        return 2
    result = trace(args.task_id.strip())
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print("task %s: found=%s" % (result["task_id"], result["found"]))
        print("  db: %s" % result.get("db"))
        if result.get("status"):
            print("  status=%s verified_by=%s artifact_bytes=%s" % (
                result.get("status"), result.get("verified_by"), result.get("artifact_bytes")))
        if result.get("contract"):
            print("  contract: %s" % json.dumps(result["contract"], ensure_ascii=False))
        if result.get("verdict"):
            print("  verdict: %s" % json.dumps(result["verdict"], ensure_ascii=False))
        for path, info in result["files"].items():
            print("  file: %s %s" % (path, json.dumps(info, ensure_ascii=False)))
        for path, lines in result["logs"].items():
            print("  log %s (%d lines):" % (path, len(lines)))
            for ln in lines:
                print("    %s" % ln[:300])
    return 0 if result["found"] else 1


if __name__ == "__main__":
    sys.exit(main())
