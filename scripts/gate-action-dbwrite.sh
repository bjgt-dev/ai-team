#!/usr/bin/env python3
# gate-action-dbwrite.sh — /act 写生产库 的执行器（gatesvc 身份运行）
# 用法：gate-action-dbwrite.sh <task-slug>
# 从 central_tasks 查 title=<slug> 的任务 payload，payload 须含 {"dbwrite": {"sql": "..."}}。
# 只允许单条 UPDATE/INSERT（白名单动词），禁止 DROP/DELETE/TRUNCATE/ALTER/CREATE/GRANT/REVOKE，
# 禁止多语句（分号），只写 central_queue 库。失败即 exit 1（fail closed）。
import json, os, re, subprocess, sys

SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
FORBIDDEN = re.compile(r"\b(DROP|DELETE|TRUNCATE|ALTER|CREATE|GRANT|REVOKE|VACUUM|COPY|CALL|DO)\b", re.I)
ALLOWED_START = re.compile(r"^\s*(UPDATE|INSERT)\s+", re.I)


def fail(msg):
    print("gate-action-dbwrite: " + msg, file=sys.stderr)
    sys.exit(1)


def db_env():
    d = {}
    for line in open("/etc/noesis/gate-db.env"):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def main():
    if len(sys.argv) != 2 or not SLUG_RE.match(sys.argv[1]):
        fail("bad task slug")
    slug = sys.argv[1]
    cfg = db_env()
    env = dict(os.environ)
    env["PGPASSWORD"] = cfg.get("DB_PASSWORD", "")
    r = subprocess.run(
        ["psql", "-h", cfg.get("DB_HOST", "172.17.0.3"), "-p", cfg.get("DB_PORT", "5432"),
         "-U", cfg.get("DB_USER", "gatesvc"), "-d", cfg.get("DB_NAME", "central_queue"),
         "-t", "-A", "-c",
         "SELECT payload FROM public.central_tasks WHERE title='%s' ORDER BY created_at DESC LIMIT 1" % slug.replace("'", "''")],
        capture_output=True, text=True, env=env, timeout=30)
    if r.returncode != 0 or not r.stdout.strip():
        fail("task not found: " + slug)
    try:
        sql = json.loads(r.stdout.strip())["dbwrite"]["sql"]
    except Exception:
        fail("payload missing dbwrite.sql")
    if not isinstance(sql, str) or not sql.strip():
        fail("sql empty")
    if ";" in sql.rstrip().rstrip(";"):
        fail("multi-statement not allowed")
    sql = sql.strip().rstrip(";")
    if not ALLOWED_START.match(sql):
        fail("only single UPDATE/INSERT allowed")
    if FORBIDDEN.search(sql):
        fail("forbidden keyword in sql")
    r2 = subprocess.run(
        ["psql", "-h", cfg.get("DB_HOST", "172.17.0.3"), "-p", cfg.get("DB_PORT", "5432"),
         "-U", cfg.get("DB_USER", "gatesvc"), "-d", cfg.get("DB_NAME", "central_queue"),
         "-v", "ON_ERROR_STOP=1", "-c", sql],
        capture_output=True, text=True, env=env, timeout=60)
    if r2.returncode != 0:
        fail("sql failed: " + r2.stderr[:300])
    print("gate-action-dbwrite: OK " + r2.stdout.strip()[:200])
    sys.exit(0)


main()
