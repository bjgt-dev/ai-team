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
    "决定栏铁律（2026-11-23 决定 14 修，误标重灾区）：只有“某人在某时拍板"
    "决定了某事”才进决定栏，必须能说出是谁、何时、决定了什么，三者缺一"
    "不可。日志原文、定时任务行、规则重述、职责/流程描述、观察结论、状态"
    "快照一律不进决定栏；本批无符合的决定时只输出一行“决定：无”，不要"
    "拿别的句子凑数。\n"
    "同一句话只写一次：不要把同一内容同时写进决定和事实（或事实和未完成），"
    "重复内容只保留在事实栏。\n"
    "无可抽取条目时只输出一行：无可抽取条目；有任何一条有效条目时不输出这行。\n"
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


def _norm_body(text):
    """Normalize an item body for duplicate comparison: drop whitespace and
    common punctuation so near-identical repeats (e.g. differing by two
    chars) still match. Used only to drop duplicates, never to rewrite."""
    import re
    return re.sub(r"[\s，。、；：,.!！?？·\-_—/\\()（）\[\]【】\"'“”‘’]+", "", text)


def _clean_extracted(text):
    """Normalize model output: some free models emit literal '\\n' and echo
    the instruction header ('分类：'). Keep only real item lines.
    2026-11-23 决定 14 修：模型常把同一句话同时写进决定和事实（K2 五组
    重文）、把规则/职责/观察写进决定栏、把同一批日志反复抽取（K7）。
    语义误标靠提示词铁律拦；此处再做确定性去重兜底：(a) 有有效条目时
    丢弃“无可抽取条目”行；(b) 同一内容（归一化后相同）只保留第一次出现，
    决定栏与事实栏重文时保留事实栏那行。"""
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
    if not lines:
        return text.strip()
    # (a) “无可抽取条目”是空结果标记：有其他有效条目时丢弃
    # （含“未完成：无可抽取条目。”这种带栏位的写法，旧版漏网）。
    def _is_empty_marker(ln):
        body = ln.split("：", 1)[1].strip() if "：" in ln else ln
        return _norm_body(body) == "无可抽取条目"
    items = [ln for ln in lines if not _is_empty_marker(ln)]
    if items:
        lines = items
    # (b) 跨栏去重：同一归一化内容只留第一次出现。
    # 事实栏优先：若某内容先出现在决定栏、后出现在事实栏，保留事实栏那行；
    # 但“无”是空栏位标记，只留第一次出现、不做决定→事实替换
    # （决定：无本身就是有效信号，事实：无没有信息量）。
    seen = {}
    keep = []
    for ln in lines:
        body = ln.split("：", 1)[1].strip() if "：" in ln else ln
        key = _norm_body(body)
        if not key:
            continue
        if key in seen:
            prev = seen[key]
            if key != "无" and prev.startswith("决定：") and ln.startswith("事实："):
                keep[keep.index(prev)] = ln
            # 其余重复一律丢弃（决定/事实重文、事实/未完成重文、整行反复）。
            continue
        seen[key] = ln
        keep.append(ln)
    return "\n".join(keep)


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
