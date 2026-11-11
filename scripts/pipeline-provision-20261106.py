#!/usr/bin/env python3
"""pipeline-provision-20261106.py — automatic workdir provisioning (formal pipeline, DESIGN §1-2).

Replaces manual deliver_file.py runs. Delivers exactly the files listed in a
manifest JSON to worker-1, verifies sha256 per file on the worker, writes a
receipt. No manual file delivery on the happy path.

Manifest format:
  {"task_id": "<slug>", "files": [{"hub_path": "/home/ubuntu/ai-team/...",
    "worker_rel": "code-agent-pilot/workdir/<dir>/<file>"}]}
worker_rel is relative to worker home (~). deliver_file.py is invoked as a
subprocess per file (atomic .part -> mv, sha verified by deliver_file itself);
this script additionally verifies sha256sum on the worker via hub_exec.py.

Usage: python3 scripts/pipeline-provision-20261106.py --manifest <manifest.json>
  --receipt-dir state/pipeline-formal-20261106
Exit 0 = all files delivered + sha-verified; 1 = any failure (receipt ok=false).
No DB writes, no queue claims, no secrets in output (sha only).
"""
import argparse, hashlib, json, os, re, subprocess, sys, time

BASE = "/home/ubuntu/ai-team"
DELIVER = BASE + "/state/pull-once-20261105/deliver_file.py"
HUB_EXEC = BASE + "/state/pull-once-20261105/hub_exec.py"


def _ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _worker_sha(worker_rel):
    cmd = "sha256sum ~/%s" % worker_rel
    r = subprocess.run(["python3", HUB_EXEC], input=cmd, capture_output=True,
                       text=True, timeout=180)
    out = r.stdout or ""
    # hub_exec prints HTTP 200 + JSON {"stdout": "...sha  path..."}
    try:
        body = json.loads(out.split("\n", 1)[1]) if "\n" in out else {}
        s = (body.get("stdout") or "").strip().split()
        return s[0] if s else ""
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--receipt-dir", default=BASE + "/state/pipeline-formal-20261106")
    a = ap.parse_args()
    man = json.load(open(a.manifest))
    task_id = man.get("task_id", "unknown")
    files = man.get("files", [])
    results, ok = [], True
    for f in files:
        hub, rel = f["hub_path"], f["worker_rel"]
        entry = {"hub_path": hub, "worker_rel": rel}
        if not os.path.exists(hub):
            entry.update(ok=False, reason="hub file missing")
            ok = False; results.append(entry); continue
        want = _sha(hub)
        entry["sha256"] = want
        dst = "~/" + rel
        r = subprocess.run(["python3", DELIVER, hub, dst], capture_output=True,
                           text=True, timeout=300)
        if r.returncode != 0 or want not in (r.stdout or ""):
            entry.update(ok=False, reason="deliver failed rc=%d out=%s" % (r.returncode, (r.stdout or "")[:200]))
            ok = False; results.append(entry); continue
        got = _worker_sha(rel)
        entry["worker_sha256"] = got
        entry["ok"] = (got == want)
        if got != want:
            ok = False
        results.append(entry)
    receipt = {"ts": _ts(), "task_id": task_id, "ok": ok, "files": results}
    os.makedirs(a.receipt_dir, exist_ok=True)
    rp = os.path.join(a.receipt_dir, "provision-%s.json" % re.sub(r"[^A-Za-z0-9_-]", "_", str(task_id)))
    json.dump(receipt, open(rp, "w"), ensure_ascii=False, indent=2)
    print("PROVISION task=%s ok=%s files=%d receipt=%s" % (task_id, ok, len(results), rp))
    for e in results:
        print("  %s ok=%s sha=%s" % (e["worker_rel"], e.get("ok"), (e.get("sha256") or "")[:12]))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
