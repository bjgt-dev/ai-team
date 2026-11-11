import os
import re
import subprocess
import datetime
import json
import sys
import argparse

def parse_dsn(dsn):
    # DSN file line: CQ_DSN="host=.. port=.. dbname=.. user=.. password=.."
    # Strip the CQ_DSN= prefix and quotes, then extract each key
    # individually -- never split on '='.
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

def psql_query(host, port, dbname, user, password, sql):
    env = os.environ.copy()
    env['PGPASSWORD'] = password
    command = ['psql', '-h', host, '-p', port, '-d', dbname, '-U', user, '-v', 'ON_ERROR_STOP=1', '-At', '-F', '|', '-c', sql]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    stdout, stderr = process.communicate()
    if process.returncode != 0:
        raise Exception("psql query failed: " + stderr.decode())
    return stdout.decode().strip().split('\n')

def parse_ts(s):
    # Always return an aware UTC datetime (naive input is assumed UTC),
    # or None when unparseable. Fractional seconds are stripped.
    if not s:
        return None
    s = str(s).strip()
    s = re.sub(r"\.\d+", "", s, count=1)
    try:
        t = datetime.datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return t.astimezone(datetime.timezone.utc)

def classify(payload):
    failure_class = payload.get('failure_class', 'F-UNDIAG')
    return failure_class

def backoff_seconds(failure_class, retry_count):
    if failure_class == 'F-429':
        return min(900, max(30, 30 * (2 ** retry_count)))
    elif failure_class == 'F-503':
        return 0
    elif failure_class == 'F-TIMEOUT':
        return 30
    elif failure_class == 'F-EMPTY':
        return 10
    elif failure_class == 'F-FORMAT':
        return 0
    elif failure_class == 'F-ROUTING':
        return 0
    elif failure_class == 'F-CONTRACT' or failure_class == 'F-VERIFY':
        return None
    elif failure_class == 'F-UNDIAG':
        return 1
    else:
        return None

def terminal_quarantine_sql(task_id, kind, now_iso):
    """cq-scan 2026-11-07 (L2): UPDATE moving a RETRY_WAIT row to
    QUARANTINED (legal per the DB transition guard: RETRY_WAIT ->
    QUARANTINED) with a payload note. The WHERE status='RETRY_WAIT'
    guard makes it idempotent: re-running it on an already-moved row
    is a no-op. Before this fix, BLOCKED / TERMINAL_FAILED only wrote
    an outbox file and left the row in RETRY_WAIT forever -- the
    long-task-sweeper only quarantines retry_count >= max_retries, so
    a retry=1 task that went BLOCKED (age > 2h) could never leave
    RETRY_WAIT except by manual SQL. QUARANTINED is the human path:
    re-entry is via pipeline-disposition only."""
    note = json.dumps({"retrywait_terminal": kind, "terminal_at": now_iso})
    return ("UPDATE central_tasks SET status='QUARANTINED', "
            "payload = payload || '%s'::jsonb "
            "WHERE task_id='%s' AND status='RETRY_WAIT'"
            % (note.replace("'", "''"), task_id))

# F1 2026-11-10 (failure_id 12): diagnostic event payload. The audit
# trigger writes central_events payload '{}'; after a terminal quarantine
# UPDATE, stamp the just-created QUARANTINED event row, inheriting the
# last RETRY_WAIT failure class/reason. Diagnostic only: nothing reads
# event payload for decisions.
def _last_failure(host, port, dbname, user, password, task_id):
    try:
        cls = psql_query(host, port, dbname, user, password,
            "SELECT payload->>'failure_class' FROM central_events "
            "WHERE task_id='%s' AND new_status='RETRY_WAIT' AND payload <> '{}'::jsonb "
            "ORDER BY created_at DESC LIMIT 1" % task_id)
        rsn = psql_query(host, port, dbname, user, password,
            "SELECT payload->>'reason' FROM central_events "
            "WHERE task_id='%s' AND new_status='RETRY_WAIT' AND payload <> '{}'::jsonb "
            "ORDER BY created_at DESC LIMIT 1" % task_id)
        c = (cls or [""])[0].strip() or None
        r = (rsn or [""])[0].strip() or None
        return c, r
    except Exception:
        return None, None

def _stamp_terminal_event(host, port, dbname, user, password, task_id, kind):
    pc, pr = _last_failure(host, port, dbname, user, password, task_id)
    fclass = pc or "F-UNDIAG"
    reason = "retrywait-terminal " + kind
    if pr:
        reason += "; last failure: " + pr
    sql = ("UPDATE central_events SET payload = jsonb_build_object('failure_class', '%s', 'reason', '%s') "
           "WHERE event_id = (SELECT event_id FROM central_events "
           "WHERE task_id='%s' AND new_status='QUARANTINED' AND payload='{}'::jsonb "
           "ORDER BY created_at DESC LIMIT 1)"
           % (fclass.replace("'", "''"), reason[:300].replace("'", "''"), task_id))
    try:
        psql_query(host, port, dbname, user, password, sql)
    except Exception as e:
        print("stamp-terminal-event failed %s: %s" % (task_id, e))

def claimer_status(crontab_text):
    """cq-scan 2026-11-07 (L2b): is a claimer cron entry present (and
    paused)? Comment lines are skipped: the crontab currently carries a
    COMMENT recording that cq_claim was disabled (2026-11-07 dec-004),
    and the old inline loop counted that comment as a live claimer and
    printed 'claimer: ACTIVE' while no claimer actually runs."""
    present = False
    paused = False
    for line in (crontab_text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "cq_claim" in s or "claim" in s:
            present = True
            if "PAUSED" in s:
                paused = True
    return present, paused

def decide_action(row, now):
    task_id, title, status, retry_count, max_retries, created_at, updated_at, not_before, payload_text = row
    retry_count = int(retry_count)
    max_retries = int(max_retries)
    payload = json.loads(payload_text) if payload_text else {}
    if status != 'RETRY_WAIT':
        return None
    updated = parse_ts(updated_at)
    nb = parse_ts(not_before) if not_before else None
    if updated is not None and (now - updated).total_seconds() > 2 * 3600:
        return 'BLOCKED'  # cumulative RETRY_WAIT cap 2h (overdue-2h)
    cls = classify(payload)
    if cls in ('F-503', 'F-FORMAT') and retry_count >= 1:
        return 'FALLBACK'  # one retry already spent: switch model, do not re-promote
    backoff = backoff_seconds(cls, retry_count)
    if backoff is None:
        return 'REWORK'  # F-CONTRACT / F-VERIFY / unknown: never auto-promote
    due = (nb is None or nb <= now) or (updated is not None and updated + datetime.timedelta(seconds=backoff) <= now)
    if due:
        if retry_count >= max_retries:
            return 'TERMINAL_FAILED'
        return 'PROMOTE'
    return None

def scan(dry_run, apply, out_path, drill):
    dsn_path = '/home/ubuntu/ai-team/state/.cq-dsn'
    with open(dsn_path, 'r') as file:
        dsn = file.read().strip()
    dsn_data = parse_dsn(dsn)
    host = dsn_data['host']
    port = dsn_data['port']
    dbname = dsn_data['dbname']
    user = dsn_data['user']
    password = dsn_data['password']
    now = datetime.datetime.now(datetime.timezone.utc)
    rows = psql_query(host, port, dbname, user, password, "SELECT task_id, title, status, retry_count, max_retries, created_at, updated_at, not_before, payload::text FROM central_tasks WHERE status = 'RETRY_WAIT' ORDER BY updated_at")
    summary = {'scanned_at': now.isoformat(), 'counts': {}, 'rows': [], 'skipped': 0, 'claimer_present': False, 'claimer_paused': False, 'stale_lease': []}
    for row in rows:
        if not row.strip():
            continue
        parts = row.split('|', 8)
        if len(parts) != 9:
            summary['skipped'] += 1
            continue
        row_data = parts
        task_id, title, status, retry_count, max_retries, created_at, updated_at, not_before, payload_text = row_data
        try:
            payload = json.loads(payload_text) if payload_text else {}
        except Exception:
            payload = {}
        action = decide_action(row_data, now)
        age = None
        upd = parse_ts(updated_at)
        if upd is not None:
            age = round((now - upd).total_seconds() / 60, 1)
        if action:
            summary['counts'][action] = summary['counts'].get(action, 0) + 1
        else:
            action = 'WAIT'
            summary['counts']['WAIT'] = summary['counts'].get('WAIT', 0) + 1
        summary['rows'].append({
            'task_id': task_id,
            'title': title,
            'failure_class': classify(payload),
            'retry_count': retry_count,
            'age_min': age,
            'action': action,
            'reason': 'Due' if action == 'PROMOTE' else ('overdue-2h' if action == 'BLOCKED' else 'not-due'),
        })
        print("%s | %s | retry=%s | age_min=%s | %s" % (task_id, classify(payload), retry_count, age, action))
        if apply and action == 'PROMOTE':
            # F2 2026-11-10 (failure_id 11): SQL-level belt & braces under decide_action's TERMINAL_FAILED gate.
            psql_query(host, port, dbname, user, password, "UPDATE central_tasks SET status='READY', retry_count=retry_count+1, updated_at=now() WHERE task_id='%s' AND status='RETRY_WAIT' AND retry_count < COALESCE(max_retries, 3)" % task_id)
        elif apply and action == 'BLOCKED':
            with open('/home/ubuntu/ai-team/state/outbox/retrywait-blocked-%s.md' % task_id, 'w') as file:
                file.write("Task %s is BLOCKED (RETRY_WAIT over 2h). failure_class=%s retry_count=%s next owner: 大管家\n" % (task_id, classify(payload), retry_count))
            # cq-scan 2026-11-07 (L2): outbox-only left the row stuck in
            # RETRY_WAIT; move it to QUARANTINED so a human can re-enter
            # it via pipeline-disposition. Outbox record is still written.
            psql_query(host, port, dbname, user, password,
                       terminal_quarantine_sql(task_id, "BLOCKED-overdue-2h", now.isoformat()))
            _stamp_terminal_event(host, port, dbname, user, password, task_id, "BLOCKED-overdue-2h")
            print("BLOCKED-QUARANTINED %s" % task_id)
        elif apply and action == 'TERMINAL_FAILED':
            with open('/home/ubuntu/ai-team/state/outbox/terminal-failed-%s.md' % task_id, 'w') as file:
                file.write("Task %s has reached MAX RETRIES (retry_count=%s). failure_class=%s\n" % (task_id, retry_count, classify(payload)))
            # cq-scan 2026-11-07 (L2): same terminal move (belt & braces
            # with the long-task-sweeper, which quarantines retry_count
            # >= max_retries on its own 5-min cycle).
            psql_query(host, port, dbname, user, password,
                       terminal_quarantine_sql(task_id, "TERMINAL_FAILED-max-retries", now.isoformat()))
            _stamp_terminal_event(host, port, dbname, user, password, task_id, "TERMINAL_FAILED-max-retries")
            print("TERMINAL-QUARANTINED %s" % task_id)
        elif apply and action == 'FALLBACK':
            with open('/home/ubuntu/ai-team/state/outbox/retrywait-fallback-%s.md' % task_id, 'w') as file:
                file.write("Task %s FALLBACK: switch model across suppliers. failure_class=%s retry_count=%s\n" % (task_id, classify(payload), retry_count))
        if upd is not None and (now - upd).total_seconds() > 1800:
            summary['stale_lease'].append(task_id)
    # Claimer check (real source: crontab, not task titles).
    try:
        crontab = subprocess.run(['crontab', '-l'], capture_output=True, text=True).stdout
    except Exception:
        crontab = ''
    summary['claimer_present'], summary['claimer_paused'] = claimer_status(crontab)
    if summary['claimer_present'] and not summary['claimer_paused']:
        print("claimer: ACTIVE")
    elif summary['claimer_present']:
        print("claimer: PAUSED")
    else:
        print("NO_ACTIVE_CLAIMER")
    # Dry-run still writes its summary report (a report is not a production write).
    # cq-scan 2026-11-07 (L2): create the parent dir so a cron run (whose
    # cwd is $HOME, not ~/ai-team) does not die on a missing directory.
    _outdir = os.path.dirname(os.path.abspath(out_path))
    if _outdir:
        os.makedirs(_outdir, exist_ok=True)
    with open(out_path, 'w') as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)
    print("scanned=%d counts=%s skipped=%d" % (len(summary['rows']), summary['counts'], summary['skipped']))
    if drill:
        print("Drill procedure:")
        print("1. Insert a RETRY_WAIT row with drill_canary=true")
        print("2. Run --apply")
        print("3. Verify the row is READY")
        print("4. Delete the canary row")
    return 0

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--drill', action='store_true')
    # cq-scan 2026-11-07 (L2): absolute default. The old relative default
    # resolved against the caller's cwd; under cron (cwd=$HOME) the summary
    # landed in ~/state/... or failed, so the last real run on record is
    # the 2026-11-06 11:50 manual one. Absolute path makes --apply cron-safe.
    parser.add_argument('--out', default='/home/ubuntu/ai-team/state/retrywait-scan-20261105/last-run.json')
    args = parser.parse_args()
    dry_run = args.dry_run
    apply = args.apply
    out_path = args.out
    drill = args.drill
    try:
        return scan(dry_run, apply, out_path, drill)
    except Exception as e:
        print("Error:", e)
        return 2

if __name__ == '__main__':
    sys.exit(main())