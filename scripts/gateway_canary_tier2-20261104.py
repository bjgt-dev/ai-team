#!/usr/bin/env python3
"""Order15 Tier-2 canary (2026-11-04). Tier-1 standard UNCHANGED.
Tier-2 pass = token echo + 3-key JSON, BOTH rounds pass, each latency <=120s.
Chinese 200-char test NOT required. Family (returned model) recorded; a
substituted returned model fails the round (same anti-substitution rule as T1).
Results appended to state/gateway-canary-tier2-20261104.jsonl, no keys, no full text.
"""
import argparse, json, secrets, time, urllib.request
OPENCODFIG = "/home/ubuntu/.config/opencode/opencode.json"
GATEWAY = "http://127.0.0.1:3001/v1"
OUT = "/home/ubuntu/ai-team/state/gateway-canary-tier2-20261104.jsonl"

def token():
    c = json.load(open(OPENCODFIG))
    for p in c.get("provider", {}).values():
        opt = p.get("options") or {}
        if opt.get("apiKey"):
            return opt["apiKey"]
        if p.get("apiKey"):
            return p["apiKey"]
    return ""

def call(mid, content, max_tokens, timeout, trace_label=None, trace_round=None):
    t0 = time.time()
    body = json.dumps({"model": mid, "messages": [{"role": "user", "content": content}],
                       "max_tokens": max_tokens, "temperature": 0}).encode()
    req = urllib.request.Request(GATEWAY + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json"})
    http_status = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            http_status = r.status
            d = json.loads(r.read().decode() or "{}")
        ch = (d.get("choices") or [])
        msg = (ch[0].get("message") or {}) if ch else {}
        content_out, returned, error = (msg.get("content") or ""), d.get("model", ""), ""
    except Exception as e:
        http_status = getattr(e, "code", None)
        content_out, returned, error = "", "", str(e)[:200]
    elapsed = time.time() - t0
    # M9-TRACE-P2: one line per model call (never breaks canary).
    try:
        if trace_label and trace_round:
            import sys as _sys
            _sys.path.insert(0, "/home/ubuntu/ai-team/scripts")
            import trace_20261105 as _trace
            _trace.emit(task_id="gwcanary-tier2-%s-%s-%s" % (trace_round, mid.replace("/", "-").replace(":", "-"), trace_label),
                        model_id=mid, latency_ms=int(round(elapsed * 1000)),
                        source="gateway-canary-tier2", http_status=http_status,
                        returned_model=returned, timeout_s=timeout,
                        content=content_out, error=error,
                        timed_out=("timed out" in error.lower()),
                        expect_json=(trace_label == "t2"))
    except Exception:
        pass
    return content_out, returned, error, elapsed

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True)
    ap.add_argument("--round", required=True)
    a = ap.parse_args()
    for mid in [m.strip() for m in a.models.split(",") if m.strip()]:
        rounds = []
        for rnd in (1, 2):
            time.sleep(3)
            tok = "K" + secrets.token_hex(6)
            c1, rm1, e1, l1 = call(mid, "只回复口令：" + tok, 16, 130, trace_label="t1", trace_round=a.round + "-r%d" % rnd)
            t1 = bool(c1.strip()) and tok in c1 and l1 <= 120
            c2, rm2, e2, l2 = call(mid, "只输出JSON，不要其他文字，含3个键：task、result、ok，值自定", 128, 130, trace_label="t2", trace_round=a.round + "-r%d" % rnd)
            try:
                s = c2.strip()
                if s.startswith("```"):
                    s = "\n".join(s.splitlines()[1:-1]) if len(s.splitlines()) > 2 else s
                j2 = json.loads(s)
                t2 = isinstance(j2, dict) and {"task", "result", "ok"} <= set(j2.keys()) and l2 <= 120 and bool(c2.strip())
            except Exception:
                t2 = False
            returned = rm1 or rm2 or ""
            base = mid[:-3] if mid.endswith("-cc") else mid
            family_ok = bool(returned) and (returned == mid or returned == base)
            rounds.append({"round": rnd, "t1_token": t1, "t2_json": t2,
                           "lat1_s": round(l1, 2), "lat2_s": round(l2, 2),
                           "family_ok": family_ok,
                           "pass": bool(t1 and t2 and family_ok),
                           "error": (e1 or e2 or "")[:120]})
        rec = {"tier": "T2", "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
               "model": mid, "rounds": rounds,
               "pass2": all(r["pass"] for r in rounds)}
        print(json.dumps(rec, ensure_ascii=False), flush=True)
        with open(OUT, "a", encoding="utf-8") as of:
            of.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
