#!/usr/bin/env python3
"""gate-extractor: read new raw_log rows and extract decisions/facts/
constraints/open items into ~/ai-team/knowledge/<inbox>/extract-*.md.

State: ~/ai-team/state/gate/extractor-state.json  {"last_seq": N}
Runs every 5 min via /etc/cron.d/gate-extractor as gatesvc.

Model: hub gateway http://127.0.0.1:3001/v1, cheapest free tier verified
2026-11-10: google/gemma-4-26b-a4b-it:free (cost 0, clean extraction);
fallback glm-4-flash. Token: /etc/noesis/extractor-token first, else
~/ai-team/state/.newapi-token. The token value is NEVER logged or written
to any output file.

DB read: same helper as kb-index-sync (gate-db.env DSN first, else
docker exec ops-pg psql). State advances only after the extract file is
written; a failed model call leaves last_seq untouched for retry.
"""
import base64
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

HUB = Path("/home/ubuntu/ai-team")
STATE_DIR = HUB / "state" / "gate"
STATE_FILE = STATE_DIR / "extractor-state.json"
INBOX = HUB / "knowledge" / "收件箱"
GATEWAY = "http://127.0.0.1:3001/v1/chat/completions"
MODEL = "google/gemma-4-26b-a4b-it:free"
FALLBACK_MODEL = "glm-4-flash"
DB = "central_queue"
BATCH = 200
CST = timezone(timedelta(hours=8))

PROMPT = (
    "你是枢纽记录员。从下面多条原始记录中抽取有价值条目，只输出条目，"
    "不要复述原文、不要编造、不要推断未写明的事。\n"
    "分类（每条一行，格式固定）：\n"
    "决定：<一句话>\n事实：<一句话>\n约束：<一句话>\n未完成：<一句话>\n"
    "无可抽取条目时只输出一行：无可抽取条目。\n"
    "只保留有实质内容的条目，寒暄、重复、无信息量的行跳过。\n\n{records}\n"
)


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


def _sql_lit(s):
    return "'" + s.replace("'", "''") + "'"


def _run_psql(sql):
    base = ["docker", "exec", "-i", "ops-pg", "psql", "-U", "postgres",
            "-d", DB, "-v", "ON_ERROR_STOP=1", "-A", "-t", "-F", "|"]
    for cmd in (base, ["sudo", "-n"] + base):
        try:
            r = subprocess.run(cmd, input=sql, capture_output=True,
                               text=True, timeout=120)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return 1, "", str(e)
        if r.returncode == 0:
            return r.returncode, r.stdout, r.stderr
        last = r
        if "permission denied" not in (r.stderr or "").lower():
            return r.returncode, r.stdout, r.stderr
    return last.returncode, last.stdout, last.stderr


def fetch_new_rows(last_seq):
    sql = (
        "SELECT seq, seat, session, role, "
        "encode(convert_to(content, 'UTF8'), 'base64') "
        "FROM raw_log WHERE seq > %d ORDER BY seq LIMIT %d;"
        % (last_seq, BATCH)
    )
    dsn = _dsn_from_env()
    if dsn:
        import psycopg
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT seq, seat, session, role, content FROM raw_log "
                    "WHERE seq > %s ORDER BY seq LIMIT %s", (last_seq, BATCH))
                return [(r[0], r[1], r[2], r[3], r[4]) for r in cur.fetchall()]
    rc, out, err = _run_psql(sql)
    if rc != 0:
        raise RuntimeError("psql read failed rc=%s: %s" % (rc, err.strip()))
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("|", 4)
        if len(parts) != 5:
            continue
        seq, seat, session, role, b64 = parts
        try:
            content = base64.b64decode(b64).decode("utf-8", "replace")
        except Exception:
            content = ""
        rows.append((int(seq), seat, session, role, content))
    return rows


def _read_token():
    for p in ("/etc/noesis/extractor-token", str(HUB / "state" / ".newapi-token")):
        try:
            tok = Path(p).read_text().strip()
            if tok:
                return tok
        except OSError:
            continue
    return ""


def call_model(records_text):
    token = _read_token()
    if not token:
        raise RuntimeError("no gateway token found (extractor-token/.newapi-token)")
    body = {
        "model": MODEL,
        "messages": [{"role": "user",
                      "content": PROMPT.format(records=records_text)}],
        "temperature": 0.2,
        "max_tokens": 1500,
    }
    last_err = None
    for model in (MODEL, FALLBACK_MODEL):
        body["model"] = model
        req = urllib.request.Request(
            GATEWAY,
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer " + token,
                     "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"].get("content") or ""
            return model, content.strip()
        except Exception as e:
            last_err = e
    raise RuntimeError("gateway call failed for both models: %s" % last_err)


def _clean_extracted(text):
    """Normalize model output: some free models emit literal '\\n' and echo
    the instruction header ('分类：'). Keep only real item lines."""
    if not text:
        return ""
    text = text.replace("\\n", "\n").replace("\\r", "")
    keep_prefixes = ("决定：", "事实：", "约束：", "未完成：", "无可抽取条目")
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(keep_prefixes):
            lines.append(line)
        elif line in ("分类：", "分类:"):
            continue
    return "\n".join(lines) if lines else text.strip()


def load_state():
    try:
        return json.loads(STATE_FILE.read_text()).get("last_seq", 0)
    except (OSError, ValueError, AttributeError):
        return 0


def save_state(last_seq):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # in-place rewrite (no rename) so file owner/perms survive cron-as-gatesvc
    with open(STATE_FILE, "w") as f:
        json.dump({"last_seq": last_seq}, f)
        f.write("\n")


def main():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    last_seq = load_state()
    rows = fetch_new_rows(last_seq)
    if not rows:
        print("gate-extractor: no new rows after seq=%d" % last_seq)
        return 0
    first, last = rows[0][0], rows[-1][0]
    records = []
    for seq, seat, session, role, content in rows:
        records.append("[seq=%d seat=%s session=%s role=%s]\n%s"
                       % (seq, seat, session, role, content[:2000]))
    records_text = "\n\n".join(records)[:12000]
    model, extracted = call_model(records_text)
    now = datetime.now(CST)
    stamp = now.strftime("%Y%m%d-%H%M")
    INBOX.mkdir(parents=True, exist_ok=True)
    out_path = INBOX / ("extract-%s.md" % stamp)
    header = (
        "# 抽取记录 extract-%s\n\n" % stamp
        + "- 来源：raw_log seq %d–%d（共 %d 行）\n" % (first, last, len(rows))
        + "- 抽取时间：%s\n" % now.strftime("%Y-%m-%d %H:%M CST")
        + "- 模型：%s（枢纽网关免费档）\n\n" % model
    )
    body = _clean_extracted(extracted) if extracted else "无可抽取条目。"
    out_path.write_text(header + body.strip() + "\n", encoding="utf-8")
    save_state(last)
    print("gate-extractor: seq=%d-%d rows=%d model=%s file=%s"
          % (first, last, len(rows), model, out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
