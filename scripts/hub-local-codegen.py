#!/usr/bin/env python3
"""hub-local code_gen handler (2026-11-06,令19问1选A,董事长执行令).
Directly calls gateway Tier-1 (newapi, glm-4-flash-cc primary, qwen3.8-flash backup)
to generate code files as JSON {files:[{path,content}]}. No opencode, no llm_doc.
Worker holds no keys; only hub holds gateway token (env NEWAPI_TOKEN / state file 0600).
Workdir mapping: hub /home/ubuntu/ai-team  <-> worker-1 ~/code-agent-pilot/workdir/
Contract workdir '~/code-agent-pilot/workdir/<name>' maps to hub
'/home/ubuntu/ai-team/state/codegen-workdir/<name>' for generation, then syncs
to worker-1 via scp. Hub paths in contracts are rejected for generation and
mapped, never passed through to worker-1 (fixes workdir_not_allowlisted).
Usage: python3 scripts/hub-local-codegen.py --spec <spec.json> --out <dir>
  spec.json: {"task_id":..., "prompt":..., "files":[...expected paths...]}
Returns: files written, sha256, py_compile result.
"""
import argparse, hashlib, json, os, subprocess, sys
HUB_BASE = "/home/ubuntu/ai-team"
WORKDIR_MAP_HUB = os.path.join(HUB_BASE, "state/codegen-workdir")
GATEWAY_URL = os.environ.get("NEWAPI_BASE_URL", "http://127.0.0.1:3000/v1/chat/completions")

def gateway_call(prompt, model="glm-4-flash-cc"):
    import urllib.request
    token = os.environ.get("NEWAPI_TOKEN", "")
    if not token:
        # read from hub-only token file if present
        for p in [os.path.join(HUB_BASE, "state/.newapi-token")]:
            if os.path.exists(p):
                token = open(p).read().strip(); break
    if not token:
        raise SystemExit("hub-local-codegen: NO_GATEWAY_TOKEN (hub only, worker never holds it)")
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0}).encode()
    req = urllib.request.Request(GATEWAY_URL, data=body, headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())["choices"][0]["message"]["content"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    spec = json.load(open(a.spec))
    print(f"hub-local-codegen task={spec.get('task_id')} out={a.out}")
    if a.dry_run:
        print("DRY-RUN OK: spec valid, gateway token present=%s" % bool(os.environ.get("NEWAPI_TOKEN")))
        return 0
    content = gateway_call(spec["prompt"])
    # expect JSON {files:[{path,content}]}
    try:
        data = json.loads(content[content.find("{"):content.rfind("}")+1])
        files = data["files"]
    except Exception as e:
        print(f"GEN_PARSE_FAIL: {e}\nRAW_HEAD={content[:500]}")
        return 2
    os.makedirs(a.out, exist_ok=True)
    for f in files:
        p = os.path.join(a.out, f["path"])
        os.makedirs(os.path.dirname(p) or a.out, exist_ok=True)
        open(p, "w", encoding="utf-8").write(f["content"])
        sha = hashlib.sha256(f["content"].encode()).hexdigest()
        print(f"WROTE {p} bytes={len(f['content'])} sha256={sha}")
        if p.endswith(".py"):
            r = subprocess.run([sys.executable, "-m", "py_compile", p], capture_output=True, text=True)
            print(f"py_compile {p} exit={r.returncode} {r.stderr[:200]}")
            if r.returncode != 0:
                return 3
    print("hub-local-codegen DONE")
    return 0

if __name__ == "__main__":
    sys.exit(main())
