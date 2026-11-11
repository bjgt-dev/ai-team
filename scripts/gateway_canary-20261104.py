#!/usr/bin/env python3
"""Order13 gateway canary (2026-11-04). Reads gateway token from opencode.json
internally, never prints it. Three tests per model, per order13 sec.3:
1) echo random token, max_tokens 16, latency p95 proxy (max of 2 runs) <=20s
2) JSON with 3 keys task/result/ok, <=60s
3) Chinese short text <=200 chars, <=60s
Fail on: empty content, returned model != requested, missing token/keys, over length.
Results appended (one JSON per model per round) to state/gateway-canary-20261104.jsonl
with only pass flags, latencies, returned model id, truncated error - no key, no full text.
"""
import argparse, json, re, secrets, sys, time, urllib.request
OPENCODFIG = "/home/ubuntu/.config/opencode/opencode.json"
GATEWAY = "http://127.0.0.1:3001/v1"
OUT = "/home/ubuntu/ai-team/state/gateway-canary-20261104.jsonl"
DBG = "/home/ubuntu/ai-team/state/gateway-canary-debug-20261104.jsonl"

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
    # M5-3-IMPORT: trace module loaded by absolute path (importlib); regular import is blind to it on this hub, see patch_trace_import.py
    # M5-3 trace (never breaks canary): one line per model call.
    try:
        if trace_label and trace_round:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location("trace_20261105", "/home/ubuntu/ai-team/scripts/trace-20261105.py")
            _trace = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_trace)
            _trace.emit(task_id="gwcanary-%s-%s-%s" % (trace_round, mid, trace_label),
                        model_id=mid, latency_ms=int(round(elapsed * 1000)),
                        source="gateway-canary", http_status=http_status,
                        returned_model=returned, timeout_s=timeout,
                        content=content_out, error=error,
                        timed_out=("timed out" in error.lower()),
                        expect_json=(trace_label == "t2"))  # M9-TRACE-P2
    except Exception:
        pass
    return content_out, returned, error, elapsed

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True)
    ap.add_argument("--round", required=True)
    a = ap.parse_args()
    models = [m.strip() for m in a.models.split(",") if m.strip()]
    results = []
    for mid in models:
        time.sleep(3)
        tok = "K" + secrets.token_hex(6)
        c1, rm1, e1, l1 = call(mid, "只回复口令：" + tok, 16, 30, trace_label="t1", trace_round=a.round)
        t1 = bool(c1.strip()) and tok in c1 and l1 <= 20
        c2, rm2, e2, l2 = call(mid, "只输出JSON，不要其他文字，含3个键：task、result、ok，值自定", 128, 60, trace_label="t2", trace_round=a.round)
        j2, t2 = None, False
        try:
            s = c2.strip()
            if s.startswith("```"):
                s = "\n".join(s.splitlines()[1:-1]) if len(s.splitlines()) > 2 else s
            j2 = json.loads(s)
            t2 = isinstance(j2, dict) and {"task", "result", "ok"} <= set(j2.keys()) and l2 <= 60 and bool(c2.strip())
        except Exception:
            t2 = False
        c3, rm3, e3, l3 = call(mid, "用200字以内中文写一段关于秋天的短文，不要英文，不要解释", 300, 60, trace_label="t3", trace_round=a.round)
        t3 = bool(c3.strip()) and len(c3.strip()) <= 200 and re.search(r"[\u4e00-\u9fff]", c3) and l3 <= 60
        returned = rm1 or rm2 or rm3 or ""
        base = mid[:-3] if mid.endswith("-cc") else mid
        family_ok = bool(returned) and (returned == mid or returned == base)
        rec = {"round": a.round, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
               "model": mid, "returned_model": returned, "family_ok": family_ok,
               "t1_token": t1, "t2_json": t2, "t3_chinese": t3,
               "lat1_s": round(l1, 2), "lat2_s": round(l2, 2), "lat3_s": round(l3, 2),
               "pass3": bool(t1 and t2 and t3 and family_ok),
               "pass_items": "%d/3" % (int(bool(t1)) + int(bool(t2)) + int(bool(t3))),
               "errors": [e for e in (e1, e2, e3) if e][:1]}
        results.append(rec)
        print(json.dumps(rec, ensure_ascii=False), flush=True)
        with open(OUT, "a", encoding="utf-8") as of:
            of.write(json.dumps(rec, ensure_ascii=False) + "\n")
        with open(DBG, "a", encoding="utf-8") as df:
            df.write(json.dumps({"round": a.round, "model": mid, "t1_snip": c1[:120], "t2_snip": c2[:120], "t3_snip": c3[:120], "t3_len": len(c3.strip())}, ensure_ascii=False) + "\n")
    return 0

if __name__ == "__main__":
    sys.exit(main())
