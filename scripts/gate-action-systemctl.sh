#!/usr/bin/env python3
# gate-action-systemctl.sh — /act systemctl 的执行器（gatesvc 身份运行）
# 用法：gate-action-systemctl.sh <task-slug>
# 从 central_tasks 查 title=<slug> 的任务 payload，payload 须含 {"systemctl": {"unit": "...", "action": "restart"|"start"|"stop"|"reload"}}。
# 只允许白名单 unit（用户级服务与 gate-svc），只允许 restart/start/stop/reload；用 sudo systemctl（gatesvc 需 sudo 免密，见 /etc/sudoers.d/gatesvc）。
# 失败即 exit 1。
import json, os, re, subprocess, sys

SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
UNIT_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,127}\.(service|timer)$")
ALLOWED_UNITS = {
    "gate-svc.service", "openclaw.service",
}
ALLOWED_USER_UNITS = {
    "queue-api.service",
}
ALLOWED_ACTIONS = {"restart", "start", "stop", "reload"}


def fail(msg):
    print("gate-action-systemctl: " + msg, file=sys.stderr)
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
        sc = json.loads(r.stdout.strip())["systemctl"]
        unit, action = sc["unit"], sc["action"]
    except Exception:
        fail("payload missing systemctl{unit,action}")
    if not UNIT_RE.match(unit):
        fail("bad unit: " + unit)
    if unit in ALLOWED_UNITS:
        # Polkit first (2026-11-11, Plan A): gate-svc runs with
        # NoNewPrivileges=yes so sudo is blocked; the polkit rule
        # /etc/polkit-1/rules.d/49-gatesvc-manage-units.rules authorizes
        # gatesvc to manage only these units (restart/start/stop/reload).
        # Fall back to sudo (works only if NoNewPrivileges is ever removed).
        cmds = [["systemctl", action, unit],
                ["sudo", "systemctl", action, unit]]
    elif unit in ALLOWED_USER_UNITS:
        # NOTE (2026-11-11): still sudo-only; NoNewPrivileges blocks it, and
        # there is no polkit path into another user's user-instance bus.
        # Residual limitation, documented in the Plan A report.
        cmds = [["sudo", "-u", "ubuntu", "/home/ubuntu/ai-team/scripts/restart-user-service.sh", action, unit]]
    else:
        fail("unit not whitelisted: " + unit)
    if action not in ALLOWED_ACTIONS:
        fail("action not whitelisted: " + action)
    last_err = ""
    for cmd in cmds:
        r2 = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r2.returncode == 0:
            print("gate-action-systemctl: %s %s OK (via %s)" % (action, unit, cmd[0]))
            sys.exit(0)
        last_err = "%s: %s" % (" ".join(cmd[:2]), (r2.stderr or r2.stdout or "")[:200])
    fail("systemctl failed: " + last_err)
    sys.exit(0)


main()
