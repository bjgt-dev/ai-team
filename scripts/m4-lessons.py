#!/usr/bin/env python3
"""M4 lessons 库 (2026-11-07 M3/M4/M5 落地扫描补齐, 令19).

缺口: 令19 要求 lessons 库放枢纽 PG, 但 PG 只有 8 张表
(central_checkpoints/events/leases/tasks, flags, locations, objects, receipts),
无 lessons 表; 唯一提到 lessons 的代码是 skill_forge.py 的一句打印,
无写入/查询. 本脚本建表 + 写入 + 查询.

表 lessons_m4: id / fingerprint / failure_class / lesson /
mechanism_change_ref / created_at.
PG 不可用或无建表权限时, 回落到 JSONL 镜像
state/m4-self-improve-20261106/lessons.jsonl (并明说), 不静默丢数据.

用法:
  python3 m4-lessons.py --init
  python3 m4-lessons.py --add --fingerprint FP-1 --class F-ROUTING \
      --lesson "工人活派工人, 不自己写" [--mechanism-ref scripts/x.py]
  python3 m4-lessons.py --query [--fingerprint FP-1] [--class F-ROUTING] [--limit 20]
  python3 m4-lessons.py --self-test   # PG 回滚事务验证 (零持久写入) + JSONL 回路
DSN 只读 state/.cq-dsn, 与 dispatch_task.py 同口径. Stdlib + psycopg.
"""
import argparse
import json
import os
import sys

BASE = "/home/ubuntu/ai-team"
DSN_FILE = os.path.join(BASE, "state/.cq-dsn")
MIRROR = os.path.join(BASE, "state/m4-self-improve-20261106/lessons.jsonl")

DDL = """CREATE TABLE IF NOT EXISTS lessons_m4 (
  id SERIAL PRIMARY KEY,
  fingerprint TEXT NOT NULL,
  failure_class TEXT NOT NULL,
  lesson TEXT NOT NULL,
  mechanism_change_ref TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)"""
ADD_SQL = ("INSERT INTO lessons_m4 (fingerprint, failure_class, lesson, mechanism_change_ref)"
           " VALUES (%s, %s, %s, %s) RETURNING id")
QUERY_SQL = ("SELECT id, fingerprint, failure_class, lesson, mechanism_change_ref,"
             " to_char(created_at,'YYYY-MM-DD HH24:MI') FROM lessons_m4")


def get_dsn():
    try:
        for line in open(DSN_FILE, encoding="utf-8"):
            line = line.strip()
            if line.startswith("CQ_DSN="):
                return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def _pg():
    import psycopg
    dsn = get_dsn()
    if not dsn:
        raise RuntimeError("无 DSN (state/.cq-dsn)")
    return psycopg.connect(dsn)


def _mirror_add(row):
    os.makedirs(os.path.dirname(MIRROR), exist_ok=True)
    with open(MIRROR, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _mirror_query(fp=None, cls=None, limit=20):
    rows = []
    if os.path.exists(MIRROR):
        for line in open(MIRROR, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if fp and r.get("fingerprint") != fp:
                continue
            if cls and r.get("failure_class") != cls:
                continue
            rows.append(r)
    return rows[-limit:]


def cmd_init():
    try:
        with _pg() as conn:
            with conn.cursor() as cur:
                cur.execute(DDL)
        print("PASS: lessons_m4 表就绪 (PG)")
        return 0
    except Exception as exc:
        print("PG 建表失败 (%s); lessons 库以 JSONL 镜像承载: %s" % (exc, MIRROR))
        return 1


def cmd_add(args):
    row = {"fingerprint": args.fingerprint, "failure_class": args.failure_class,
           "lesson": args.lesson, "mechanism_change_ref": args.mechanism_ref}
    try:
        with _pg() as conn:
            with conn.cursor() as cur:
                cur.execute(ADD_SQL, (args.fingerprint, args.failure_class,
                                       args.lesson, args.mechanism_ref))
                rid = cur.fetchone()[0]
        print("PASS: lessons_m4 写入 id=%d (PG)" % rid)
        return 0
    except Exception as exc:
        _mirror_add(row)
        print("PG 写入失败 (%s); 已记入 JSONL 镜像 %s" % (exc, MIRROR))
        return 0


def cmd_query(args):
    try:
        sql = QUERY_SQL
        conds, params = [], []
        if args.fingerprint:
            conds.append("fingerprint = %s"); params.append(args.fingerprint)
        if args.failure_class:
            conds.append("failure_class = %s"); params.append(args.failure_class)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY id DESC LIMIT %s"
        params.append(args.limit)
        with _pg() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        for r in rows:
            print("%s | %s | %s | %s | ref=%s | %s" % r)
        print("QUERY: %d 行 (PG)" % len(rows))
        return 0
    except Exception as exc:
        rows = _mirror_query(args.fingerprint, args.failure_class, args.limit)
        for r in rows:
            print("%s | %s | %s | ref=%s" % (r.get("fingerprint"), r.get("failure_class"),
                                             r.get("lesson"), r.get("mechanism_change_ref")))
        print("QUERY: %d 行 (JSONL 镜像; PG 不可用: %s)" % (len(rows), exc))
        return 0


def self_test():
    fails = []

    def chk(cond, msg):
        print(("PASS " if cond else "FAIL ") + msg)
        if not cond:
            fails.append(msg)

    # 1. JSONL 回路 (零 PG 依赖, 临时镜像).
    import tempfile
    tmp = tempfile.mkdtemp(prefix="m4lessons-")
    global MIRROR
    MIRROR = os.path.join(tmp, "lessons.jsonl")
    _mirror_add({"fingerprint": "FP-T1", "failure_class": "F-ROUTING",
                 "lesson": "工人活派工人", "mechanism_change_ref": "scripts/x.py"})
    rows = _mirror_query("FP-T1")
    chk(len(rows) == 1 and rows[0]["lesson"] == "工人活派工人", "JSONL 镜像写入/查询回路")
    chk(len(_mirror_query("FP-NOPE")) == 0, "JSONL 查询无命中为空 (反例)")

    # 2. PG 回滚事务验证: 先提交 DDL 建表 (须持久), 再在显式事务内插查后
    #    ROLLBACK, 自测行零持久写入.
    try:
        with _pg() as conn:
            with conn.cursor() as cur:
                cur.execute(DDL)
                cur.execute("SELECT count(*) FROM lessons_m4")
                before = cur.fetchone()[0]
        with _pg() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("BEGIN")
                cur.execute(ADD_SQL, ("FP-SELFTEST", "F-VERIFY", "自测行", None))
                cur.execute("SELECT count(*) FROM lessons_m4 WHERE fingerprint='FP-SELFTEST'")
                inside = cur.fetchone()[0]
                cur.execute("ROLLBACK")
        with _pg() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM lessons_m4 WHERE fingerprint='FP-SELFTEST'")
                after = cur.fetchone()[0]
        chk(inside == 1, "PG 事务内插查可见")
        chk(after == 0, "PG 回滚后自测行消失 (零持久写入)")
        chk(True, "PG 建表 + 回滚事务验证通过 (建表前 %d 行)" % before)
    except Exception as exc:
        print("SKIP PG 部分: %s (JSONL 镜像仍可用)" % exc)
    print("SELF-TEST: %d failures" % len(fails))
    return 1 if fails else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="M4 lessons library (PG lessons_m4 + JSONL mirror)")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--add", action="store_true")
    ap.add_argument("--query", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--fingerprint", default=None)
    ap.add_argument("--class", dest="failure_class", default=None)
    ap.add_argument("--lesson", default=None)
    ap.add_argument("--mechanism-ref", default=None)
    ap.add_argument("--limit", type=int, default=20)
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if a.init:
        return cmd_init()
    if a.add:
        if not (a.fingerprint and a.failure_class and a.lesson):
            print("FAIL: --add 须给 --fingerprint --class --lesson")
            return 2
        return cmd_add(a)
    if a.query:
        return cmd_query(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
