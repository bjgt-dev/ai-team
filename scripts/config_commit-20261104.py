#!/usr/bin/env python3
"""ORDER13 config_commit (daily): desensitized copies of opencode.json and
gateway channel summary (no keys) committed to a LOCAL git repo at
state/config-history (not the whole ai-team tree). apiKey values replaced
with ***REDACTED***; channel key column never read."""
import json, os, subprocess, re
BASE = "/home/ubuntu/ai-team"
REPO = os.path.join(BASE, "state/config-history")
os.makedirs(REPO, exist_ok=True)
cfg = json.load(open("/home/ubuntu/.config/opencode/opencode.json"))
def redact(o):
    if isinstance(o, dict):
        return {k: ("***REDACTED***" if k.lower() in ("apikey", "api_key", "key", "token") else redact(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [redact(x) for x in o]
    return o
json.dump(redact(cfg), open(os.path.join(REPO, "opencode.json"), "w"), ensure_ascii=False, indent=2)
audit = json.load(open(os.path.join(BASE, "state/gateway-audit.json")))
ch = [{"id": c["id"], "name": c["name"], "type": c["type"], "status": c["status"],
       "priority": c["priority"], "weight": c["weight"], "group": c["group"], "models": c["models"]} for c in audit["channels"]]
json.dump({"date": audit["date"], "upstream_count": audit["upstream_count"], "seat": audit["seat"], "channels": ch},
          open(os.path.join(REPO, "gateway-channels.json"), "w"), ensure_ascii=False, indent=2)
def run(*a):
    return subprocess.run(a, cwd=REPO, capture_output=True, text=True)
if not os.path.exists(os.path.join(REPO, ".git")):
    run("git", "init", "-q")
    run("git", "config", "user.name", "order13-config")
    run("git", "config", "user.email", "config@localhost")
run("git", "add", "-A")
r = run("git", "-c", "user.name=order13-config", "-c", "user.email=config@localhost", "commit", "-q", "-m", "config " + audit["date"])
print("commit rc:", r.returncode, (r.stdout + r.stderr)[:200])
print(run("git", "log", "--oneline", "-3").stdout)
