#!/usr/bin/env python3
"""Legacy queue -> central_tasks import (DRAFT, 2026-11-02, NOT run yet).

Mapping follows A2's gap table + chairman order 02 s3 (missing fields =
UNKNOWN, never fabricated). Input: a JSON export of old queue items
(list of objects). Default mode is DRY-RUN: it prints the mapped rows as
JSON for human review and writes NOTHING to any database. --apply needs
CQ_DSN from the environment and uses parameterized INSERTs only.

Mapping rules (each is a window-confirmable assumption, not a fact):
- old readable id -> payload.old_id + payload.legacy=true; new task_id is
  a fresh UUID (schema default). Old ids are never forced into UUIDs.
- title: item title, else old_id, else "UNKNOWN" (title is NOT NULL).
- status: PENDING -> READY. DONE is NEVER imported as DONE: old DONE rows
  lack compatible evidence (see below), so every old DONE becomes
  QUARANTINED + payload.legacy_done=true, for human disposition at the
  window (release QUARANTINED->READY or re-verify). Low-cred DONE
  additionally gets payload.suspect=true.
- priority (string -> SMALLINT): default map P0=0, high=1, normal=2,
  low=3. Unmapped strings -> 2 + payload.priority_original + flag.
  The map MUST be confirmed at the window (A2: mapping TBD).
- retry_count: old attempt if int >= 0 else 0; max_retries: old max if
  present else 3.
- worker_class: kept only if in (main, backup, local, emergency);
  otherwise 'main' + payload.worker_class_original + flag.
- claim_token (16-hex string) CANNOT fit BIGINT current_fencing_token:
  preserved verbatim in payload.claim_token_original, fencing set to 0,
  payload.fencing_incompatible=true. None tokens get the same treatment
  with payload.claim_token_missing=true. No numeric token is invented.
- artifact: old items carry no sha/bytes, so NO checkpoint rows are
  created; payload.artifact_missing=true.
- lane: old items have no lane -> NULL + payload.lane_unknown=true.
- not_before/owner/decision_deadline: NULL unless the item supplies them;
  default_on_timeout='NO_CHANGE' (butler default).
"""
import argparse
import json
import os
import sys

PRIORITY_DEFAULT = {"P0": 0, "high": 1, "normal": 2, "low": 3}
WORKER_CLASSES = {"main", "backup", "local", "emergency"}


def map_item(item, prio_map, suspect_ids):
    payload = {"legacy": True, "old_id": item.get("id"),
               "old_status": item.get("status")}
    for k in ("claim_token_original", "worker_class_original",
              "priority_original", "artifact_missing", "lane_unknown",
              "fencing_incompatible", "claim_token_missing", "legacy_done",
              "suspect"):
        payload.pop(k, None)
    old_status = item.get("status")
    title = item.get("title") or item.get("id") or "UNKNOWN"
    if old_status == "PENDING":
        status = "READY"
    elif old_status == "DONE":
        status = "QUARANTINED"
        payload["legacy_done"] = True
        if item.get("id") in suspect_ids:
            payload["suspect"] = True
    else:
        status = "QUARANTINED"
        payload["legacy_done"] = False
    prio_raw = item.get("priority")
    if isinstance(prio_raw, int):
        priority = prio_raw
    elif prio_raw in prio_map:
        priority = prio_map[prio_raw]
    else:
        priority = 2
        if prio_raw is not None:
            payload["priority_original"] = prio_raw
    attempt = item.get("attempt")
    retry_count = attempt if isinstance(attempt, int) and attempt >= 0 else 0
    max_retries = item.get("max_retries")
    if not (isinstance(max_retries, int) and max_retries >= 0):
        max_retries = 3
    wc = item.get("worker_class")
    if wc not in WORKER_CLASSES:
        if wc is not None:
            payload["worker_class_original"] = wc
        wc = "main"
    token = item.get("claim_token")
    if token is None:
        payload["claim_token_missing"] = True
    else:
        payload["claim_token_original"] = str(token)
        payload["fencing_incompatible"] = True
    payload["artifact_missing"] = True
    payload["lane_unknown"] = True
    row = {"title": title, "payload": payload, "worker_class": wc,
           "status": status, "priority": priority,
           "retry_count": retry_count, "max_retries": max_retries,
           "current_fencing_token": 0,
           "not_before": item.get("not_before"),
           "owner": item.get("owner"),
           "decision_deadline": item.get("decision_deadline"),
           "default_on_timeout": "NO_CHANGE"}
    return row


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("export_json", help="JSON list of old queue items")
    ap.add_argument("--priority-map", default="{}",
                    help="JSON overrides for the default priority map")
    ap.add_argument("--suspect-ids", default="[]",
                    help="JSON list of low-cred DONE old ids -> suspect flag")
    ap.add_argument("--apply", action="store_true",
                    help="Actually insert (needs CQ_DSN); default is dry-run")
    a = ap.parse_args(argv)
    items = json.load(open(a.export_json))
    prio_map = dict(PRIORITY_DEFAULT)
    prio_map.update(json.loads(a.priority_map))
    suspect_ids = set(json.loads(a.suspect_ids))
    rows = [map_item(it, prio_map, suspect_ids) for it in items]
    if not a.apply:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        print(f"# DRY-RUN: {len(rows)} rows mapped, 0 written. "
              "Review, confirm the priority map + suspect list, then --apply.",
              file=sys.stderr)
        return 0
    import psycopg
    dsn = os.environ.get("CQ_DSN", "")
    if not dsn:
        print("CQ_DSN not set; refusing to apply", file=sys.stderr)
        return 2
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(
                    """INSERT INTO central_tasks
                       (title, payload, worker_class, status, priority,
                        retry_count, max_retries, current_fencing_token,
                        not_before, owner, decision_deadline,
                        default_on_timeout)
                       VALUES (%s, %s::jsonb, %s, %s, %s, %s, %s, %s,
                               %s, %s, %s, %s)""",
                    (r["title"], json.dumps(r["payload"]), r["worker_class"],
                     r["status"], r["priority"], r["retry_count"],
                     r["max_retries"], r["current_fencing_token"],
                     r["not_before"], r["owner"], r["decision_deadline"],
                     r["default_on_timeout"]))
        conn.commit()
    print(f"APPLIED: {len(rows)} rows inserted.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
