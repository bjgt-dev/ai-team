#!/usr/bin/env python3
# gate-action-sshvps.sh — /act ssh-vps 的执行器（gatesvc 身份运行）
# 用法：gate-action-sshvps.sh <task-slug>
# 从 central_tasks 查 title=<slug> 的任务 payload，payload 须含 {"sshvps": {"host": "...", "command": "..."}}。
# host 只允许白名单别名；command 只允许白名单命令（固定命令 + 安全参数），不接受任意 shell。
# 密钥 /etc/noesis/gatesvc-ssh-key（S3 时配发，缺失即 fail closed）。失败即 exit 1。
import json, os, re, subprocess, sys

SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
ALLOWED_HOSTS = {"amd-proxy", "arm-agent", "instabox-worker-1"}
# 白名单命令：固定命令模板，参数只允许 [A-Za-z0-9_.\-/] 字符
ALLOWED_COMMANDS = [
    (re.compile(r"^systemctl (status|restart|is-active) [a-z0-9][a-z0-9_.\-]*\.service$"), "systemctl"),
    (re.compile(r"^df -h$"), "df"),
    (re.compile(r"^uptime$"), "uptime"),
    (re.compile(r"^ls /home/ubuntu/ai-team/scripts/$"), "ls"),
]
KEY_FILE = "/etc/noesis/gatesvc-ssh-key"


def fail(msg):
    print("gate-action-sshvps: " + msg, file=sys.stderr)
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
        sv = json.loads(r.stdout.strip())["sshvps"]
        host, command = sv["host"], sv["command"]
    except Exception:
        fail("payload missing sshvps{host,command}")
    if host not in ALLOWED_HOSTS:
        fail("host not whitelisted: " + host)
    if not any(pat.match(command) for pat, _ in ALLOWED_COMMANDS):
        fail("command not whitelisted: " + command)
    if not os.path.isfile(KEY_FILE):
        fail("gatesvc ssh key missing (S3 not done): " + KEY_FILE)
    # Target VPS user is ubuntu; gatesvc is only the local service account.
    r2 = subprocess.run(
        ["ssh", "-i", KEY_FILE, "-o", "StrictHostKeyChecking=yes",
         "-o", "ConnectTimeout=30", "-o", "BatchMode=yes",
         "ubuntu@" + host, command],
        capture_output=True, text=True, timeout=120)
    if r2.returncode != 0:
        fail("ssh failed rc=%d: %s" % (r2.returncode, r2.stderr[:300]))
    print("gate-action-sshvps: %s %s OK\n%s" % (host, command, r2.stdout[:500]))
    sys.exit(0)


main()
