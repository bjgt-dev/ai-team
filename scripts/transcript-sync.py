#!/usr/bin/env python3
"""transcript-sync: append new transcripts / worker-log lines to raw_log.

Sources:
  - OpenCode: ~/.local/share/opencode/opencode.db (SQLite, message + part;
    session_message is empty in practice -- verified 2026-11-10).
    seat=opencode, session=<opencode session_id>, role=user|assistant.
  - OpenClaw: /opt/openclaw-official/.openclaw (sudo read; acpx session
    JSONs + agents/*/sessions/*.jsonl). As of 2026-11-10 there is NO live
    readable transcript there: acpx messages are empty and all 251 session
    files are .deleted.*.zst from 2026-10-19/20. The script reads whatever
    is readable and records the honest status in state; it never invents.
  - Worker logs: ~/ai-team/state/logs/*.log, new lines only.
    seat=worker, session=worker:<basename>, role=log.

State: ~/ai-team/state/gate/transcript-state.json
  {"opencode": {"last_created": ms, "last_id": ""},
   "worker": {path: {"offset": N, "inode": I}},
   "openclaw": {"status": "...", "checked_at": "..."}}
First run sets worker offsets to EOF (no history backfill) and the opencode
watermark to the current max (no backfill); only newer rows are imported.

DB insert: gate-db.env DSN first, else `docker exec ops-pg psql`
(existing hub convention). Content goes through base64 so any text is safe.
"""
import base64
import glob
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

HUB = Path("/home/ubuntu/ai-team")
STATE_DIR = HUB / "state" / "gate"
STATE_FILE = STATE_DIR / "transcript-state.json"
OPENCODE_DB = Path("/home/ubuntu/.local/share/opencode/opencode.db")
OPENCLAW_DIR = Path("/opt/openclaw-official/.openclaw")
WORKER_LOG_DIR = HUB / "state" / "logs"
DB = "central_queue"
BATCH = 500
MAX_CONTENT = 8000
MAX_LINE = 2000
CST = timezone(timedelta(hours=8))


# ---------------- DB helper (same convention as kb-index-sync) ----------------
def _parse_env_file(path):
    vals = {}
    try:
        for line in Path(path).read_text(errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return vals


def _dsn_from_env():
    if os.environ.get("GATE_DB_DSN"):
        return os.environ["GATE_DB_DSN"]
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    for p in ("/etc/noesis/gate-db.env", "/etc/noesis/db-gatesvc.env"):
        vals = _parse_env_file(p)
        if vals.get("GATE_DB_DSN") or vals.get("DATABASE_URL"):
            return vals.get("GATE_DB_DSN") or vals.get("DATABASE_URL")
        pw = vals.get("PGPASSWORD") or vals.get("DB_PASSWORD")
        user = vals.get("PGUSER") or vals.get("DB_USER")
        if pw and user:
            return (
                "host={host} port={port} dbname={db} user={user} password={pw}".format(
                    host=vals.get("PGHOST") or vals.get("DB_HOST", "172.17.0.3"),
                    port=vals.get("PGPORT") or vals.get("DB_PORT", "5432"),
                    db=vals.get("PGDATABASE") or vals.get("DB_NAME", DB),
                    user=user,
                    pw=pw,
                )
            )
    return None


def _run_psql(sql):
    base = ["docker", "exec", "-i", "ops-pg", "psql", "-U", "postgres",
            "-d", DB, "-v", "ON_ERROR_STOP=1", "-A", "-t"]
    for cmd in (base, ["sudo", "-n"] + base):
        try:
            r = subprocess.run(cmd, input=sql, capture_output=True,
                               text=True, timeout=180)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return 1, "", str(e)
        if r.returncode == 0:
            return r.returncode, r.stdout, r.stderr
        last = r
        if "permission denied" not in (r.stderr or "").lower():
            return r.returncode, r.stdout, r.stderr
    return last.returncode, last.stdout, last.stderr


def insert_rows(rows):
    """rows: list of (seat, session, role, content). Returns inserted count."""
    if not rows:
        return 0
    dsn = _dsn_from_env()
    if dsn:
        import psycopg
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO raw_log(seat, session, role, content) "
                    "VALUES (%s, %s, %s, %s)", rows)
            conn.commit()
        return len(rows)
    total = 0
    for i in range(0, len(rows), 200):
        chunk = rows[i:i + 200]
        vals = []
        for seat, session, role, content in chunk:
            b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
            vals.append("(%s, %s, %s, convert_from(decode('%s', 'base64'), 'UTF8'))"
                        % (_lit(seat), _lit(session), _lit(role), b64))
        sql = ("INSERT INTO raw_log(seat, session, role, content) VALUES\n"
               + ",\n".join(vals) + ";\n")
        rc, out, err = _run_psql(sql)
        if rc != 0:
            raise RuntimeError("psql insert failed rc=%s: %s" % (rc, err.strip()))
        total += len(chunk)
    return total


def _lit(s):
    return "'" + str(s).replace("'", "''") + "'"


# ---------------- state ----------------
def load_state():
    try:
        st = json.loads(STATE_FILE.read_text())
        if isinstance(st, dict):
            return st
    except (OSError, ValueError):
        pass
    return {}


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:  # in-place rewrite, keeps owner/perms
        json.dump(state, f, ensure_ascii=False, indent=1)
        f.write("\n")


# ---------------- OpenCode ----------------
def _part_text(data):
    """Extract readable text from one part.data JSON value."""
    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        return ""
    for key in ("text", "content"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v
        if isinstance(v, list):
            parts = []
            for item in v:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item, str):
                    parts.append(item)
            if parts:
                return "\n".join(parts)
    nested = data.get("state")
    if isinstance(nested, dict) and isinstance(nested.get("text"), str):
        return nested["text"]
    return ""


def sync_opencode(state):
    if not OPENCODE_DB.exists():
        return 0, "opencode.db missing"
    oc = state.setdefault("opencode", {})
    conn = sqlite3.connect("file:%s?mode=ro" % OPENCODE_DB, uri=True)
    try:
        cur = conn.cursor()
        if "last_created" not in oc:
            # first run: watermark at current max, no backfill
            row = cur.execute("SELECT max(time_created) FROM message").fetchone()
            oc["last_created"] = row[0] or 0
            oc["last_id"] = ""
            return 0, "first run, watermark=%s (no backfill)" % oc["last_created"]
        last_created = oc.get("last_created", 0)
        last_id = oc.get("last_id", "")
        rows = cur.execute(
            "SELECT id, session_id, time_created, data FROM message "
            "WHERE (time_created > ?) OR (time_created = ? AND id > ?) "
            "ORDER BY time_created, id LIMIT ?",
            (last_created, last_created, last_id, BATCH)).fetchall()
        if not rows:
            return 0, "no new messages"
        to_insert = []
        for mid, session_id, tc, data_raw in rows:
            try:
                data = json.loads(data_raw)
            except ValueError:
                data = {}
            role = data.get("role") or "assistant"
            parts = cur.execute(
                "SELECT data FROM part WHERE message_id = ? ORDER BY time_created",
                (mid,)).fetchall()
            texts = [_part_text(json.loads(p[0])) for p in parts
                     if _safe_json(p[0])]
            text = "\n".join(t for t in texts if t).strip()
            if not text:
                # fall back to any text carried directly on the message
                text = _part_text(data)
            if text:
                to_insert.append(("opencode", session_id, role,
                                  text[:MAX_CONTENT]))
        n = insert_rows(to_insert)
        oc["last_created"] = rows[-1][2]
        oc["last_id"] = rows[-1][0]
        return n, "scanned=%d inserted=%d" % (len(rows), n)
    finally:
        conn.close()


def _safe_json(s):
    try:
        json.loads(s)
        return True
    except ValueError:
        return False


# ---------------- OpenClaw ----------------
def _sudo_read(path):
    r = subprocess.run(["sudo", "-n", "cat", str(path)],
                       capture_output=True, timeout=60)
    if r.returncode != 0:
        return None
    return r.stdout


def sync_openclaw(state):
    """Best effort. Returns (inserted, status string)."""
    oc = state.setdefault("openclaw", {})
    try:
        r = subprocess.run(
            ["sudo", "-n", "find", str(OPENCLAW_DIR / "acpx" / "sessions"),
             "-maxdepth", "1", "-name", "*.json"],
            capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            status = "无权限（sudo find 失败）"
            oc["status"] = status
            return 0, status
        files = [l for l in r.stdout.splitlines() if l.strip()]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        status = "无权限（sudo 不可用）"
        oc["status"] = status
        return 0, status
    seen = oc.setdefault("seen_counts", {})
    total = 0
    for f in sorted(files):
        raw = _sudo_read(f)
        if raw is None:
            continue
        try:
            d = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        msgs = d.get("messages") or []
        done = seen.get(f, 0)
        new = msgs[done:]
        rows = []
        for m in new:
            if not isinstance(m, dict):
                continue
            text = m.get("content") or m.get("text") or ""
            if isinstance(text, list):
                text = "\n".join(
                    x.get("text", "") if isinstance(x, dict) else str(x)
                    for x in text)
            if not str(text).strip():
                continue
            rows.append(("openclaw",
                         d.get("acp_session_id") or Path(f).stem,
                         m.get("role") or "assistant",
                         str(text)[:MAX_CONTENT]))
        total += insert_rows(rows)
        seen[f] = len(msgs)
    # live session transcripts only: *.jsonl under agents/*/sessions/
    r = subprocess.run(
        ["sudo", "-n", "find", str(OPENCLAW_DIR / "agents"),
         "-path", "*/sessions/*.jsonl", "!", "-name", "*.deleted.*"],
        capture_output=True, text=True, timeout=60)
    live = [l for l in (r.stdout or "").splitlines() if l.strip()]
    status = ("acpx sessions=%d messages_imported=%d live_jsonl=%d "
              "(sessions dir holds only .deleted.*.zst from 2026-10-19/20; "
              "no live readable OpenClaw transcript)" % (len(files), total, len(live)))
    oc["status"] = status
    oc["checked_at"] = datetime.now(CST).strftime("%Y-%m-%d %H:%M CST")
    return total, status


# ---------------- worker logs ----------------
def sync_worker_logs(state):
    wk = state.setdefault("worker", {})
    rows = []
    note = []
    for path in sorted(WORKER_LOG_DIR.glob("*.log")):
        try:
            st = path.stat()
        except OSError:
            continue
        key = str(path)
        ent = wk.get(key)
        if not ent or ent.get("inode") != st.st_ino:
            # first sight (or rotated): start at EOF, no backfill
            wk[key] = {"offset": st.st_size, "inode": st.st_ino}
            note.append("%s: first sight, offset=%d (no backfill)"
                        % (path.name, st.st_size))
            continue
        offset = ent.get("offset", 0)
        if st.st_size < offset:  # truncated/rotated
            offset = 0
        if st.st_size == offset:
            continue
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
        wk[key] = {"offset": st.st_size, "inode": st.st_ino}
        lines = data.decode("utf-8", "replace").splitlines()
        lines = [l for l in lines if l.strip()][:1000]
        for l in lines:
            rows.append(("worker", "worker:%s" % path.name, "log",
                         l[:MAX_LINE]))
    n = insert_rows(rows)
    return n, "; ".join(note) if note else "new lines inserted=%d" % n


def main():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    results = []
    try:
        n, note = sync_opencode(state)
        results.append("opencode: %d (%s)" % (n, note))
    except Exception as e:
        results.append("opencode: ERROR %s" % e)
    try:
        n, note = sync_openclaw(state)
        results.append("openclaw: %d (%s)" % (n, note))
    except Exception as e:
        results.append("openclaw: ERROR %s" % e)
    try:
        n, note = sync_worker_logs(state)
        results.append("worker: %d (%s)" % (n, note))
    except Exception as e:
        results.append("worker: ERROR %s" % e)
    save_state(state)
    print("transcript-sync: " + " | ".join(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
