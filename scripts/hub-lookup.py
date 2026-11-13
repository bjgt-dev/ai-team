#!/usr/bin/env python3
"""hub-lookup.py — find where a hub component lives (read-only).

The seat's existence-check tool (AGENTS.md: existence claims must check
the source first). Searches state/hub-registry.json AND the live hub
(scripts/ listing, live crontab, verdict dirs, state/ listing).
Usage: python3 scripts/hub-lookup.py <query>
Exit 0 = found, 1 = not found, 2 = registry unreadable. Stdlib only.
"""
import json
import os
import subprocess
import sys

BASE = "/home/ubuntu/ai-team"
REG = os.path.join(BASE, "state", "hub-registry.json")


def _run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout or ""
    except Exception:
        return -1, ""


def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("usage: hub-lookup.py <query>")
        return 2
    q = sys.argv[1].strip().lower()
    try:
        reg = json.load(open(REG, encoding="utf-8"))
    except Exception as e:
        print("FAIL: registry unreadable: %s" % e)
        return 2
    found = False

    for c in reg.get("canonical_paths", []):
        blob = " ".join([str(c.get("artifact", "")), str(c.get("canonical", "")),
                         str(c.get("legacy", "")), str(c.get("note", ""))]).lower()
        if q in blob:
            print("REGISTRY canonical_path: %s -> %s (legacy: %s)" % (
                c.get("artifact"), c.get("canonical"), c.get("legacy") or "-"))
            found = True
    for d in reg.get("directories", []):
        blob = " ".join([str(d.get("path", "")), str(d.get("purpose", ""))]).lower()
        if q in blob:
            print("REGISTRY directory: %s — %s" % (d.get("path"), d.get("purpose")))
            found = True
    for t in reg.get("tools", []):
        blob = " ".join([str(t.get("id", "")), str(t.get("path", "")),
                         str(t.get("purpose", "")), str(t.get("caller", ""))]).lower()
        if q in blob:
            print("REGISTRY tool: %s (%s) — %s [caller: %s, status: %s]" % (
                t.get("id"), t.get("path"), t.get("purpose"), t.get("caller"), t.get("status")))
            found = True
    for c in reg.get("crons", []):
        blob = " ".join([str(c.get("id", "")), str(c.get("command", "")),
                         str(c.get("purpose", ""))]).lower()
        if q in blob:
            print("REGISTRY cron: %s (%s) — %s" % (c.get("id"), c.get("schedule"), c.get("purpose")))
            found = True
    for s in reg.get("services", []):
        blob = " ".join([str(s.get("id", "")), str(s.get("how_to_check", ""))]).lower()
        if q in blob:
            print("REGISTRY service: %s — check: %s" % (s.get("id"), s.get("how_to_check")))
            found = True

    rc, out = _run(["ls", os.path.join(BASE, "scripts")])
    for name in out.splitlines():
        if q in name.lower():
            print("LIVE script: scripts/%s" % name)
            found = True
    rc, out = _run(["crontab", "-l"])
    for line in out.splitlines():
        if q in line.lower() and not line.strip().startswith("#"):
            print("LIVE cron: %s" % line.strip()[:160])
            found = True
    for vdir in ("ceo-seat/state/butler-verdicts", "state/butler-verdicts"):
        rc, out = _run(["ls", os.path.join(BASE, vdir)])
        for name in out.splitlines():
            if q in name.lower():
                print("LIVE verdict: %s/%s" % (vdir, name))
                found = True
    rc, out = _run(["ls", os.path.join(BASE, "state")])
    for name in out.splitlines():
        if q in name.lower():
            print("LIVE state: state/%s" % name)
            found = True

    if not found:
        print("NOT FOUND: %s — no registry entry and no live match" % sys.argv[1].strip())
        return 1
    return 0


if __name__ == "__main__":
    _rc = main()
    # 证据（2026-11-13 root-gate）：存在性核查行为记入 check-ledger，只记时间+问题哈希，不记问题内容；只追加，写失败不影响主流程与退出码。
    try:
        import hashlib as _hl, json as _js, datetime as _dt
        _h = _hl.md5(sys.argv[1].encode("utf-8")).hexdigest() if len(sys.argv) > 1 else ""
        _led = "/home/ubuntu/ai-team/state/check-ledger.jsonl" if "ai-team" in os.path.dirname(os.path.abspath(__file__)) else os.path.expanduser("~/workspace/tools/check-ledger.jsonl")
        with open(_led, "a", encoding="utf-8") as _f:
            _f.write(_js.dumps({"ts": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "tool": "hub-lookup", "qhash": _h}, ensure_ascii=False) + "\n")
    except Exception:
        pass
    sys.exit(_rc)
