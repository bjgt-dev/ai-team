#!/usr/bin/env python3
# gate-action-deploy.sh — /act deploy 的执行器（gatesvc 身份运行）
# 用法：gate-action-deploy.sh <task-slug>
# 从 central_tasks 查 title=<slug> 的任务 payload，payload 须含 {"deploy": {"kind": "script"|"service", "src": "...", "dst": "..."}}。
# 只允许部署到枢纽 ~/ai-team/scripts/ 或 ~/ai-team/state/ 下（白名单目录），src 必须已存在。
# 失败即 exit 1（fail closed）。
import json, os, re, shutil, subprocess, sys

SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
ALLOWED_DST_PREFIXES = ("/home/ubuntu/ai-team/scripts/", "/home/ubuntu/ai-team/state/")
ALLOWED_SRC_PREFIXES = ("/home/ubuntu/ai-team/", "/tmp/")


def fail(msg):
    print("gate-action-deploy: " + msg, file=sys.stderr)
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
        payload = json.loads(r.stdout.strip())
        dep = payload["deploy"]
        kind, src, dst = dep["kind"], dep["src"], dep["dst"]
    except Exception:
        fail("payload missing deploy{kind,src,dst}")
    if kind not in ("script", "service"):
        fail("kind not whitelisted: " + str(kind))
    if not any(src.startswith(p) for p in ALLOWED_SRC_PREFIXES):
        fail("src not in allowed prefix: " + src)
    if not any(dst.startswith(p) for p in ALLOWED_DST_PREFIXES):
        fail("dst not in allowed prefix: " + dst)
    if not os.path.isfile(src):
        fail("src missing: " + src)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    os.chmod(dst, 0o755 if kind == "script" else 0o644)
    print("gate-action-deploy: %s -> %s OK" % (src, dst))
    sys.exit(0)


main()
