#!/usr/bin/env python3
"""Beszel health check (2026-11-07, gap-fill).

GAP IT CLOSES: Beszel was declared live 2026-11-04 (amd-proxy agent
Up, hub login 200) but NO health-check code existed anywhere on the
hub -- no script probed it, no cron watched it, a silent Beszel death
would only be noticed by hand. The 2026-11-04 decision says the chief
of staff supervises it and reports only on state colour change; this
script is the supervision.

What it checks (hub-local only; amd-proxy agent is reached through
the hub UI, not probed directly from here):
  - docker containers beszel-hub + beszel-agent-local are Up
    (skippable with --no-docker, e.g. in tests)
  - HTTP GET on the hub UI (default http://127.0.0.1:8091/, any 2xx/3xx
    counts as up; connection failure counts as down) and on the local
    agent (default http://127.0.0.1:8090/)
  - writes state/beszel-health-20261107/latest.json; on failure also
    writes one outbox alert file.
Exit codes: 0 all up, 1 any check down, 2 bad arguments.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

BASE = os.environ.get("AI_TEAM_BASE", "/home/ubuntu/ai-team")
STATE_DIR = os.path.join(BASE, "state", "beszel-health-20261107")
OUTBOX_DIR = os.path.join(BASE, "state", "outbox")


def http_up(url, timeout=10):
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, r.status
    except urllib.error.HTTPError as e:
        # Any HTTP response (even 401) proves the service is listening.
        return True, e.code
    except Exception as e:
        return False, str(e)[:120]


def docker_up(name, timeout=15):
    try:
        r = subprocess.run(["docker", "ps", "--format", "{{.Names}} {{.Status}}"],
                           capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return False, str(e)[:120]
    for line in r.stdout.splitlines():
        if line.startswith(name + " "):
            return line.split(None, 1)[1].lower().startswith("up"), line
    return False, "container not in docker ps: %s" % name


def main():
    ap = argparse.ArgumentParser(description="Beszel health check")
    ap.add_argument("--hub-url", default="http://127.0.0.1:8091/")
    ap.add_argument("--agent-url", default="http://127.0.0.1:8090/")
    ap.add_argument("--containers", default="beszel-hub,beszel-agent-local")
    ap.add_argument("--no-docker", action="store_true")
    ap.add_argument("--state-dir", default=STATE_DIR)
    ap.add_argument("--no-outbox", action="store_true")
    args = ap.parse_args()

    checks, problems = {}, []
    for label, url in (("hub_ui", args.hub_url), ("agent_local", args.agent_url)):
        up, detail = http_up(url)
        checks[label] = {"up": up, "detail": detail, "url": url}
        if not up:
            problems.append("%s down: %s (%s)" % (label, url, detail))
    if not args.no_docker:
        for name in [c for c in args.containers.split(",") if c]:
            up, detail = docker_up(name)
            checks["container_" + name] = {"up": up, "detail": detail}
            if not up:
                problems.append("container %s not Up: %s" % (name, detail))

    ok = not problems
    rec = {"ok": ok, "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "checks": checks,
           "problems": problems}
    os.makedirs(args.state_dir, exist_ok=True)
    with open(os.path.join(args.state_dir, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    if not ok and not args.no_outbox:
        os.makedirs(OUTBOX_DIR, exist_ok=True)
        fn = os.path.join(OUTBOX_DIR, "beszel-health-%s.txt"
                          % time.strftime("%Y%m%d-%H%M%S"))
        with open(fn, "w", encoding="utf-8") as f:
            f.write("Beszel health breach:\n" + "\n".join(problems) + "\n")
        rec["outbox"] = fn
    print(json.dumps(rec, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
