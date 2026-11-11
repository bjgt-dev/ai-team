#!/usr/bin/env python3
"""import-eval-cases-20261111.py -- import regression-set (20) + golden set (10)
into public.eval_cases (Eval OS phase 2, G6/G8, 2026-11-11).

Sources (hub canonical after G8 copy):
  state/eval/regression-set/index.json + 20 x eval-*.md
  state/eval-golden-20261109/golden-set.jsonl

Mapping decisions (explicit; eval_cases.task_type CHECK only allows
code / llm_doc / timer / script):
  regression category -> task_type:
    代码任务 -> code; 文档生成 / 简单查询 / 遇阻排障 -> llm_doc
    (query/debug cases judge a text answer, same as llm_doc)
  golden category -> task_type: all -> llm_doc
    (retrieval / faithfulness / json_validity / guardrail all judge a
    text/JSON answer; no golden case is a code/timer/script task)
  eval_id: regression EVAL-R-01..20 in index.json order (gap matrix M1-2);
    golden keeps its original ids (ret-001, faith-001, ...), golden=true.

Safety:
  --dry-run prints the 30 rows and validates them, writes nothing.
  --sql-out FILE writes literal INSERTs (for the postgres-admin fallback).
  Live run uses psycopg + state/.cq-dsn (same read pattern as c2-verifier),
  INSERT ... ON CONFLICT (eval_id) DO NOTHING, so re-runs are safe.
  Only touches public.eval_cases (new, exam-only table). No business data.
  Refuses to write if any row fails validation.
"""
import argparse
import json
import os
import sys

BASE = "/home/ubuntu/ai-team"
REG_DIR = os.path.join(BASE, "state", "eval", "regression-set")
GOLDEN_FILE = os.path.join(BASE, "state", "eval-golden-20261109", "golden-set.jsonl")
DSN_FILE = os.path.join(BASE, "state", ".cq-dsn")

TASK_TYPES = {"code", "llm_doc", "timer", "script"}
REG_CATEGORY_MAP = {
    "代码任务": "code",
    "文档生成": "llm_doc",
    "简单查询": "llm_doc",
    "遇阻排障": "llm_doc",
}
GOLDEN_VERIFY = {
    "retrieval": "检索 top-3 命中期望关键词",
    "faithfulness": "期望值命中且禁值不在",
    "json_validity": "文件本身逐行 JSON 合法",
    "guardrail": "MANUAL 人工复核",
}


def parse_md_input(path):
    """Return the text under the '## 输入' section of a regression case .md."""
    text = open(path, encoding="utf-8").read()
    marker = "## 输入\n"
    i = text.find(marker)
    if i < 0:
        raise ValueError("%s: no '## 输入' section" % path)
    rest = text[i + len(marker):]
    j = rest.find("\n## ")
    return rest[:j].strip() if j >= 0 else rest.strip()


def build_rows():
    rows = []
    index = json.load(open(os.path.join(REG_DIR, "index.json"), encoding="utf-8"))
    if len(index) != 20:
        raise ValueError("regression index.json has %d entries, want 20" % len(index))
    for n, entry in enumerate(index, 1):
        cat = entry["category"]
        if cat not in REG_CATEGORY_MAP:
            raise ValueError("unmapped regression category: %r" % cat)
        src = "%s.md" % entry["id"]
        rows.append({
            "eval_id": "EVAL-R-%02d" % n,
            "input": parse_md_input(os.path.join(REG_DIR, src)),
            "task_type": REG_CATEGORY_MAP[cat],
            "success_criteria": {"criteria": entry["criteria"], "source_file": src,
                                 "source_id": entry["id"], "category": cat},
            "constraints": {},
            "verify_method": "静态文本判定：grep/字数/相似度（criteria 见 success_criteria）",
            "version": 1,
            "golden": False,
        })
    golden = [json.loads(line) for line in open(GOLDEN_FILE, encoding="utf-8")
              if line.strip()]
    if len(golden) != 10:
        raise ValueError("golden-set.jsonl has %d cases, want 10" % len(golden))
    for case in golden:
        cat = case["category"]
        if cat not in GOLDEN_VERIFY:
            raise ValueError("unmapped golden category: %r" % cat)
        crit = {k: v for k, v in case.items() if k not in ("id", "category")}
        rows.append({
            "eval_id": case["id"],
            "input": case.get("query") or case.get("request") or case.get("fact") or "",
            "task_type": "llm_doc",
            "success_criteria": crit,
            "constraints": {},
            "verify_method": GOLDEN_VERIFY[cat],
            "version": 1,
            "golden": True,
        })
    for r in rows:
        if r["task_type"] not in TASK_TYPES:
            raise ValueError("%s: bad task_type %r" % (r["eval_id"], r["task_type"]))
        if not r["input"] or not r["input"].strip():
            raise ValueError("%s: empty input" % r["eval_id"])
        if not isinstance(r["success_criteria"], dict) or not r["success_criteria"]:
            raise ValueError("%s: empty success_criteria" % r["eval_id"])
        if not r["verify_method"]:
            raise ValueError("%s: empty verify_method" % r["eval_id"])
    ids = [r["eval_id"] for r in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate eval_id in rows")
    return rows


def get_dsn():
    with open(DSN_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("CQ_DSN="):
                return line[len("CQ_DSN="):].strip().strip("\"").strip("'")
    return ""


def sql_literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def insert_sql(row):
    cols = ["eval_id", "input", "task_type", "success_criteria",
            "constraints", "verify_method", "version", "golden"]
    vals = [sql_literal(row["eval_id"]), sql_literal(row["input"]),
            sql_literal(row["task_type"]),
            sql_literal(json.dumps(row["success_criteria"], ensure_ascii=False)),
            sql_literal(json.dumps(row["constraints"], ensure_ascii=False)),
            sql_literal(row["verify_method"]), sql_literal(row["version"]),
            sql_literal(row["golden"])]
    return ("INSERT INTO public.eval_cases (%s) VALUES (%s) "
            "ON CONFLICT (eval_id) DO NOTHING;" % (", ".join(cols), ", ".join(vals)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sql-out", default="")
    args = ap.parse_args()
    rows = build_rows()
    print("rows built: %d (regression %d, golden %d)"
          % (len(rows), sum(1 for r in rows if not r["golden"]),
             sum(1 for r in rows if r["golden"])))
    if args.dry_run:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        print("DRY-RUN OK: %d rows validated, nothing written" % len(rows))
        return 0
    if args.sql_out:
        with open(args.sql_out, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(insert_sql(r) + "\n")
        print("SQL written: %s (%d statements)" % (args.sql_out, len(rows)))
        return 0
    import psycopg
    dsn = get_dsn()
    if not dsn:
        print("no DSN in %s, refusing" % DSN_FILE)
        return 1
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(
                    "INSERT INTO public.eval_cases "
                    "(eval_id, input, task_type, success_criteria, constraints, "
                    "verify_method, version, golden) VALUES "
                    "(%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s) "
                    "ON CONFLICT (eval_id) DO NOTHING",
                    (r["eval_id"], r["input"], r["task_type"],
                     json.dumps(r["success_criteria"], ensure_ascii=False),
                     json.dumps(r["constraints"], ensure_ascii=False),
                     r["verify_method"], r["version"], r["golden"]))
            cur.execute("SELECT count(*) FROM public.eval_cases")
            print("eval_cases rows after import: %d" % cur.fetchone()[0])
        conn.commit()
    print("IMPORT OK: %d rows upserted (conflicts skipped)" % len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
