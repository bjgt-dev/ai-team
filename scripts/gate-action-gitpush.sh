#!/usr/bin/env python3
# gate-action-gitpush.sh — /act git-push 的执行器（gatesvc 身份运行）
# 用法：gate-action-gitpush.sh <task-slug>
# 从 central_tasks 查 title=<slug> 的任务 payload，payload 须含 {"gitpush": {"repo": "...", "branch": "..."}}。
# 只允许白名单仓库（枢纽内 git 仓库），只推指定分支；不接受任意命令。失败即 exit 1。
import json, os, re, subprocess, sys

SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-/]{0,127}$")
ALLOWED_REPOS = ("/home/ubuntu/ai-team",)


def fail(msg):
    print("gate-action-gitpush: " + msg, file=sys.stderr)
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
        gp = json.loads(r.stdout.strip())["gitpush"]
        repo, branch = gp["repo"], gp["branch"]
    except Exception:
        fail("payload missing gitpush{repo,branch}")
    if repo not in ALLOWED_REPOS:
        fail("repo not whitelisted: " + repo)
    if not BRANCH_RE.match(branch):
        fail("bad branch: " + branch)
    if not os.path.isdir(os.path.join(repo, ".git")):
        fail("not a git repo: " + repo)
    r2 = subprocess.run(["git", "-C", repo, "push", "origin", branch],
                        capture_output=True, text=True, timeout=120)
    if r2.returncode != 0:
        fail("git push failed: " + r2.stderr[:300])
    print("gate-action-gitpush: %s %s OK" % (repo, branch))
    sys.exit(0)


main()
