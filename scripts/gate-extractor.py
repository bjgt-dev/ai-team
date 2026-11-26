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

D-batch 2026-11-26 (audit-fix-batch-d): the gateway timed out on both
models every 5 min since 2026-11-25 23:16, last_seq frozen at 431079
while raw_log grew past 959k rows. Hardening added, all in this file:
- backoff: consecutive failures + cool_until kept in the state file;
  while cooling down, model calls are skipped (no empty spinning).
- single-model degrade: primary timeout 120s, fallback retried once
  with a SHORTER timeout (60s); both failing counts as one failure.
- rule-extraction fallback: when both models fail (or cooling down),
  deterministic keyword extraction still writes an extract file and
  advances last_seq, clearly marked rule-extracted / low confidence.
  last_seq is NEVER reset and extracted rows are NEVER replayed.
- catch-up mode: backlog (max_seq - last_seq) over threshold raises
  BATCH to 1000 and runs up to 5 batches per invocation (capped, so
  the gateway is never hammered); model is attempted only on the
  first batch of a run, later batches are rule-only. A non-blocking
  flock on the state dir prevents overlapping cron runs.
- C-batch follow-up: docker-psql path strips the base64 line folds
  SQL-side (replace(encode(...), chr(10), '')), same fix as
  eval_sample.py — previously a folded row was split and truncated.
"""
import base64
import fcntl
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

HUB = Path("/home/ubuntu/ai-team")
STATE_DIR = HUB / "state" / "gate"
STATE_FILE = STATE_DIR / "extractor-state.json"
LOCK_FILE = STATE_DIR / "extractor.lock"
INBOX = HUB / "knowledge" / "收件箱"
GATEWAY = "http://127.0.0.1:3001/v1/chat/completions"
MODEL = "google/gemma-4-26b-a4b-it:free"
FALLBACK_MODEL = "glm-4-flash"
DB = "central_queue"
BATCH = 200
# D-batch 2026-11-26 tunables.
MODEL_TIMEOUT = 120          # primary model timeout (s)
FALLBACK_TIMEOUT = 60        # fallback model: shorter retry, not a repeat 120s
COOLDOWN_BASE_S = 600        # first failure cools 10 min
COOLDOWN_CAP_S = 7200        # backoff caps at 2 h
CATCHUP_THRESHOLD = 5000     # backlog rows above this -> catch-up mode
CATCHUP_BATCH = 1000         # rows per batch while catching up
MAX_BATCHES_PER_RUN = 5      # cap: at most 5 batches (5000 rows) per run
RULE_MAX_ITEMS = 40          # rule extraction: max items per batch
RULE_LINE_MAX = 200          # rule extraction: max chars kept per line
# Rule fallback only claims "fact" lines; decisions need who/when/what
# and a regex cannot prove that, so everything rule-extracted lands in
# 事实： with an explicit low-confidence marker (never 决定：).
RULE_KEYWORDS = ("决定", "已决定", "拍板", "结论", "待办", "未完成",
                 "需要做", "下一步", "TODO", "FIXME")
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


def fetch_new_rows(last_seq, limit=BATCH):
    # D-batch: Postgres encode(...,'base64') folds every 76 chars with a
    # newline; splitlines() downstream would treat continuations as junk
    # and truncate each record to ~57 bytes. Strip the folds SQL-side
    # (same fix as eval_sample.py, C-batch 2026-11-26).
    sql = (
        "SELECT seq, seat, session, role, "
        "replace(encode(convert_to(content, 'UTF8'), 'base64'), chr(10), '') "
        "FROM raw_log WHERE seq > %d ORDER BY seq LIMIT %d;"
        % (last_seq, limit)
    )
    dsn = _dsn_from_env()
    if dsn:
        import psycopg
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT seq, seat, session, role, content FROM raw_log "
                    "WHERE seq > %s ORDER BY seq LIMIT %s", (last_seq, limit))
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
            # Belt-and-braces: tolerate any residual folding whitespace.
            content = base64.b64decode("".join(b64.split())).decode("utf-8", "replace")
        except Exception:
            content = ""
        rows.append((int(seq), seat, session, role, content))
    return rows


def fetch_max_seq():
    """Return max(seq) in raw_log, or None if the DB cannot be read."""
    dsn = _dsn_from_env()
    try:
        if dsn:
            import psycopg
            with psycopg.connect(dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT max(seq) FROM raw_log")
                    row = cur.fetchone()
                    return row[0] if row else None
        rc, out, err = _run_psql("SELECT max(seq) FROM raw_log;")
        if rc == 0 and out.strip():
            return int(out.strip().splitlines()[0])
    except Exception:
        return None
    return None


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
    # D-batch: single-model degrade — primary gets the full timeout,
    # the fallback gets ONE shorter retry (60s), not a second 120s wait.
    for model, timeout in ((MODEL, MODEL_TIMEOUT),
                           (FALLBACK_MODEL, FALLBACK_TIMEOUT)):
        body["model"] = model
        req = urllib.request.Request(
            GATEWAY,
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer " + token,
                     "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"].get("content") or ""
            return model, content.strip()
        except Exception as e:
            last_err = e
    raise RuntimeError("gateway call failed for both models: %s" % last_err)


def rule_extract(rows):
    """Deterministic fallback when both models fail (or while cooling
    down): keep lines containing decision/fact/conclusion markers.
    Output uses the same 事实： line format as model output so the
    existing _clean_extracted() and file format still apply, but every
    line carries a [规则抽取·低置信度] marker and NOTHING is ever
    emitted as 决定： (a regex cannot prove who/when/what)."""
    seen = set()
    items = []
    for seq, _seat, _session, _role, content in rows:
        for raw in (content or "").splitlines():
            line = raw.strip()
            if len(line) < 8 or len(line) > 2000:
                continue
            if not any(kw in line for kw in RULE_KEYWORDS):
                continue
            body = line[:RULE_LINE_MAX].strip()
            key = _norm_body(body)
            if not key or key in seen:
                continue
            seen.add(key)
            items.append("事实：[seq=%d 规则抽取·低置信度] %s" % (seq, body))
            if len(items) >= RULE_MAX_ITEMS:
                return "\n".join(items)
    return "\n".join(items)


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
    """State dict: last_seq plus D-batch backoff fields. Old files with
    only {"last_seq": N} still load (missing keys default to 0).
    Readers that only need last_seq (e.g. s5-stall-watch.py) are
    unaffected by the extra keys."""
    try:
        st = json.loads(STATE_FILE.read_text())
        if not isinstance(st, dict):
            return {"last_seq": 0, "consecutive_failures": 0, "cool_until": 0}
        return {"last_seq": int(st.get("last_seq", 0)),
                "consecutive_failures": int(st.get("consecutive_failures", 0)),
                "cool_until": int(st.get("cool_until", 0))}
    except (OSError, ValueError, AttributeError, TypeError):
        return {"last_seq": 0, "consecutive_failures": 0, "cool_until": 0}


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # in-place rewrite (no rename) so file owner/perms survive cron-as-gatesvc
    with open(STATE_FILE, "w") as f:
        json.dump({"last_seq": int(state["last_seq"]),
                   "consecutive_failures": int(state.get("consecutive_failures", 0)),
                   "cool_until": int(state.get("cool_until", 0))}, f)
        f.write("\n")


def _cooldown_for(failures):
    """Exponential backoff in seconds: 600, 1200, 2400, 4800, cap 7200."""
    return min(COOLDOWN_BASE_S * (2 ** max(failures - 1, 0)), COOLDOWN_CAP_S)


def _write_extract(first, last, rows, model_label, body_text, mode, backlog):
    now = datetime.now(CST)
    stamp = now.strftime("%Y%m%d-%H%M")
    INBOX.mkdir(parents=True, exist_ok=True)
    # Filename carries the seq range: several batches may land in the
    # same minute during catch-up, and a bare minute stamp would collide.
    out_path = INBOX / ("extract-%s-%d-%d.md" % (stamp, first, last))
    header = (
        "# 抽取记录 extract-%s-%d-%d\n\n" % (stamp, first, last)
        + "- 来源：raw_log seq %d–%d（共 %d 行）\n" % (first, last, len(rows))
        + "- 抽取时间：%s\n" % now.strftime("%Y-%m-%d %H:%M CST")
        + "- 模型：%s\n" % model_label
        + "- 模式：%s；抽取前积压：%s 行\n\n"
        % (mode, ("约 %d" % backlog) if backlog is not None else "未知")
    )
    body = _clean_extracted(body_text) if body_text else "无可抽取条目。"
    out_path.write_text(header + body.strip() + "\n", encoding="utf-8")
    return out_path


def _process_batch(rows, state, attempt_model, mode, backlog):
    """Extract one batch, write its file, advance last_seq in state.
    Returns (model_label, wrote_file). Never raises for a model
    failure: that path falls back to rule extraction."""
    first, last = rows[0][0], rows[-1][0]
    records = []
    for seq, seat, session, role, content in rows:
        records.append("[seq=%d seat=%s session=%s role=%s]\n%s"
                       % (seq, seat, session, role, content[:2000]))
    records_text = "\n\n".join(records)[:12000]
    model_label = ""
    extracted = ""
    if attempt_model:
        try:
            model, extracted = call_model(records_text)
            model_label = "%s（枢纽网关免费档）" % model
            state["consecutive_failures"] = 0
            state["cool_until"] = 0
        except Exception as e:
            failures = int(state.get("consecutive_failures", 0)) + 1
            state["consecutive_failures"] = failures
            state["cool_until"] = int(time.time()) + _cooldown_for(failures)
            print("gate-extractor: model failed (%s); failures=%d, "
                  "cooling down %ds, falling back to rule extraction"
                  % (e, failures, _cooldown_for(failures)))
            extracted = rule_extract(rows)
            model_label = ("rule-extracted（规则兜底·低置信度，"
                           "双模型失败 #%d）" % failures)
    else:
        extracted = rule_extract(rows)
        model_label = "rule-extracted（规则兜底·低置信度，冷却中/追赶批）"
    out_path = _write_extract(first, last, rows, model_label, extracted,
                              mode, backlog)
    state["last_seq"] = last
    save_state(state)
    print("gate-extractor: seq=%d-%d rows=%d model=%s file=%s"
          % (first, last, len(rows), model_label, out_path))
    return model_label, out_path


def _acquire_lock():
    """Non-blocking exclusive flock: a second cron run exits quietly
    instead of racing the first on state + output files."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def main():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_fd = _acquire_lock()
    if lock_fd is None:
        print("gate-extractor: another run holds the lock, exiting")
        return 0
    try:
        return _main_locked()
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        except OSError:
            pass


def _main_locked():
    state = load_state()
    last_seq = int(state["last_seq"])
    max_seq = fetch_max_seq()
    backlog = (max_seq - last_seq) if max_seq is not None else None
    catchup = backlog is not None and backlog > CATCHUP_THRESHOLD
    mode = "catchup" if catchup else "normal"
    batch_size = CATCHUP_BATCH if catchup else BATCH
    max_batches = MAX_BATCHES_PER_RUN if catchup else 1
    if backlog is not None:
        print("gate-extractor: last_seq=%d max_seq=%d backlog=%d mode=%s"
              % (last_seq, max_seq, backlog, mode))
    batches = 0
    total_rows = 0
    while batches < max_batches:
        rows = fetch_new_rows(int(state["last_seq"]), batch_size)
        if not rows:
            print("gate-extractor: no new rows after seq=%d" % state["last_seq"])
            return 0
        # Backoff: while cooling down, skip the gateway entirely.
        cooling = int(state.get("cool_until", 0)) > int(time.time())
        # Model is attempted only on the first batch of a run: in
        # catch-up mode later batches are rule-only, so a 180s dual
        # timeout can never stack past the 5-minute cron interval.
        attempt_model = (not cooling) and (batches == 0)
        if cooling:
            print("gate-extractor: cooling down until %d (failures=%d), "
                  "rule extraction only"
                  % (int(state.get("cool_until", 0)),
                     int(state.get("consecutive_failures", 0))))
        _process_batch(rows, state, attempt_model, mode, backlog)
        batches += 1
        total_rows += len(rows)
        if len(rows) < batch_size:
            break  # drained: fewer rows than a full batch
        if batches < max_batches:
            time.sleep(2)  # catch-up pacing: don't hammer the DB/gateway
    print("gate-extractor: run done batches=%d rows=%d last_seq=%d mode=%s"
          % (batches, total_rows, int(state["last_seq"]), mode))
    return 0


def dry_run(seq_start, seq_end, out_dir):
    """Validate output format on a small seq range without touching
    state and without calling the gateway (rule extraction only)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = fetch_new_rows(seq_start - 1, min(seq_end - seq_start + 1, 1000))
    rows = [r for r in rows if seq_start <= r[0] <= seq_end]
    if not rows:
        print("dry-run: no rows in seq %d-%d" % (seq_start, seq_end))
        return 0
    extracted = rule_extract(rows)
    body = _clean_extracted(extracted) if extracted else "无可抽取条目。"
    p = out / ("extract-dryrun-%d-%d.md" % (rows[0][0], rows[-1][0]))
    p.write_text(
        "# 抽取记录 dry-run %d-%d\n\n- 来源：raw_log seq %d–%d（共 %d 行）\n"
        "- 模型：rule-extracted（dry-run，规则兜底·低置信度）\n\n%s\n"
        % (rows[0][0], rows[-1][0], rows[0][0], rows[-1][0], len(rows),
           body.strip()), encoding="utf-8")
    print("dry-run: rows=%d file=%s" % (len(rows), p))
    print("--- file head ---")
    print("\n".join(p.read_text(encoding="utf-8").splitlines()[:15]))
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--dry-run":
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("--dry-run", action="store_true")
        ap.add_argument("--seq-start", type=int, required=True)
        ap.add_argument("--seq-end", type=int, required=True)
        ap.add_argument("--out-dir", default="/tmp/gate-extractor-dryrun")
        ns = ap.parse_args(args)
        sys.exit(dry_run(ns.seq_start, ns.seq_end, ns.out_dir))
    sys.exit(main())
