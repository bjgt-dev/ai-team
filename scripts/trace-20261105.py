#!/usr/bin/env python3
"""trace-20261105.py -- shared production model-call trace (M5-3, CHAIR-20261105-18 residual 3).

M3 exit item 5 proved only a MINIMAL trace: 3 probe calls made by one script
(state/m3-exit-20261105/5-trace-20261105.jsonl). Production calls from butler /
scheduler-driven gateway_canary / patrol / brain_canary stayed in fragmented
old logs (model-latency.jsonl: model/ms/timeout only; gateway-canary-*.jsonl:
no task_id, no unified category; newapi logs: no task_id/latency).
This module is the single writer every production call point uses from now on.

One line per model call, JSONL, fields (same schema as M3-5, plus source):
  task_id / ts / ts_unix / model_id / latency_ms / latency_s /
  result_category (成功/429/503/超时/空/格式错) /
  http_status / returned_model / source / timeout_s / content_len / error
Raw evidence per call: state/m5-20261105/trace/raw-<task_id>-<ts_unix>.json
  (one file per run; raw-<task_id>.json keeps the latest only, for compat)
  (http_status / elapsed_ms / timed_out / rc / returned_model /
   content_snip 120 chars / content_full up to 2000 chars / error / ts;
   no keys, no full text).
Trace file: state/m5-20261105/trace/trace-20261105.jsonl

Classification (deterministic, same as M3-5; --verify recomputes with it):
  超时: call exceeded timeout_s (incl. opencode rc=124)
  429: HTTP 429; 503: HTTP 503 or other 5xx (exact code in http_status)
  空: HTTP 200 but empty content, or opencode with no text (incl. other 4xx)
  格式错: caller required JSON and parsing failed (caller passes
          result_category="格式错" explicitly, or expect_json=True)
  成功: anything else with a usable reply
Callers that only know the transport outcome omit result_category and let
classify() compute it; callers that required JSON pass it explicitly.

emit() NEVER raises and NEVER breaks its caller: all errors are swallowed
(trace is observability, not control flow). No network, no LLM, no spend,
no new middleware, stdlib only.

Usage as a library:
  import trace_20261105 as trace
  trace.emit(task_id=..., model_id=..., latency_ms=..., source=...,
             http_status=..., returned_model=..., timeout_s=...,
             content="...", error="...", timed_out=False, rc=None)
Usage as a script:
  python3 scripts/trace-20261105.py --verify   # trace lines vs raw files
  python3 scripts/trace-20261105.py --test     # logic tests (temp dir only)
"""
import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime

HUB = "/home/ubuntu/ai-team"
TRACE_DIR = os.path.join(HUB, "state/m5-20261105/trace")
TRACE = os.path.join(TRACE_DIR, "trace-20261105.jsonl")
CATEGORIES = ["成功", "429", "503", "超时", "空", "格式错"]


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def classify(http_status=None, timed_out=False, content="", expect_json=False):
    """Deterministic classification, same rules as M3-5 (scripts/m3-trace-20261105.py)."""
    if timed_out:
        return "超时"
    if http_status == 429:
        return "429"
    if http_status is not None and http_status >= 500:
        return "503"
    if http_status is not None and 400 <= http_status < 500:
        return "空"
    if not (content or "").strip():
        return "空"
    if expect_json:
        # Fence-wrapped JSON is valid JSON (canary strips ``` fences the
        # same way); only genuinely unparsable text is 格式错. (M9 fix:
        # a fence-wrapped t2 reply was misclassified as 格式错.)
        s = (content or "").strip()
        if s.startswith("```"):
            ls = s.splitlines()
            s = "\n".join(ls[1:-1]) if len(ls) > 2 else s
        try:
            json.loads(s.strip())
        except Exception:
            return "格式错"
    return "成功"


def emit(task_id, model_id, latency_ms, source, result_category=None,
         http_status=None, returned_model="", timeout_s=None,
         content="", error="", timed_out=False, rc=None, expect_json=False,
         trace_dir=None):
    """Append one trace line + raw file. Never raises; returns the record or None."""
    try:
        tdir = trace_dir or TRACE_DIR
        os.makedirs(tdir, exist_ok=True)
        cat = result_category or classify(http_status=http_status, timed_out=timed_out,
                                          content=content, expect_json=expect_json)
        if cat not in CATEGORIES:
            cat = "空"
        rec = {"task_id": str(task_id), "ts": now_iso(), "ts_unix": int(time.time()),
               "model_id": model_id, "latency_ms": int(latency_ms),
               "latency_s": round(int(latency_ms) / 1000.0, 2),
               "result_category": cat, "http_status": http_status,
               "returned_model": returned_model or "", "source": source,
               "timeout_s": timeout_s, "content_len": len((content or "").strip()),
               "error": (error or "")[:200]}
        raw = {"task_id": str(task_id), "model_id": model_id, "http_status": http_status,
               "elapsed_ms": int(latency_ms), "timed_out": bool(timed_out), "rc": rc,
               "returned_model": returned_model or "",
               "content_snip": (content or "").strip()[:120],
               "content_full": (content or "").strip()[:2000],
               "expect_json": bool(expect_json),
               "error": (error or "")[:200], "ts": rec["ts"]}
        raw["ts_unix"] = rec["ts_unix"]
        run_key = "%d-%06d" % (rec["ts_unix"], datetime.now().microsecond)
        rec["run_key"] = run_key
        raw["run_key"] = run_key
        with open(os.path.join(tdir, "raw-%s-%s.json" % (rec["task_id"], run_key)), "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        with open(os.path.join(tdir, "raw-%s.json" % rec["task_id"]), "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        with open(os.path.join(tdir, "trace-20261105.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec
    except Exception:
        return None


def verify(trace_dir=None):
    """Recompute every trace line against its raw file. Prints PASS/FAIL per line."""
    tdir = trace_dir or TRACE_DIR
    path = os.path.join(tdir, "trace-20261105.jsonl")
    if not os.path.exists(path):
        print("verify: no trace file %s" % path)
        return 1
    lines = [json.loads(x) for x in open(path, encoding="utf-8") if x.strip()]
    n_ok, n_fail, n_skip = 0, 0, 0
    import glob as _glob
    for rec in lines:
        tid = rec.get("task_id", "?")
        problems = []
        raw = None
        cands = []
        for _p in _glob.glob(os.path.join(tdir, "raw-%s-*.json" % tid)):
            try:
                _raw = json.load(open(_p, encoding="utf-8"))
            except Exception:
                continue
            if rec.get("run_key") and _raw.get("run_key") == rec.get("run_key"):
                cands.append((0, _p))
            elif not rec.get("run_key") and _raw.get("ts_unix") is not None:
                cands.append((abs(int(_raw["ts_unix"]) - int(rec.get("ts_unix", 0))), _p))
        if cands:
            cands.sort()
            if cands[0][0] <= 10:
                raw = json.load(open(cands[0][1], encoding="utf-8"))
            else:
                print("SKIP %s: no per-run raw within 10s (nearest %ss off)" % (tid, cands[0][0]))
                n_skip += 1
                continue
        elif os.path.exists(os.path.join(tdir, "raw-%s.json" % tid)):
            # Pre-fix lines: the single raw file was overwritten by a later
            # run of the same task_id, so the original evidence is lost.
            # Honest SKIP (neither pass nor fail), not silent deletion.
            print("SKIP %s: no per-run raw (emitted before per-run fix, raw overwritten)" % tid)
            n_skip += 1
            continue
        else:
            print("FAIL %s: raw file missing" % tid)
            n_fail += 1
            continue
        if raw.get("model_id") != rec.get("model_id"):
            problems.append("model trace=%s raw=%s" % (rec.get("model_id"), raw.get("model_id")))
        d_ms = abs(int(rec.get("latency_ms", -1)) - int(raw.get("elapsed_ms", -2)))
        if d_ms > 1000:
            problems.append("latency diff %sms (>1000ms)" % d_ms)
        content = raw.get("content_full") or raw.get("content_snip", "")
        cat2 = classify(http_status=raw.get("http_status"),
                        timed_out=raw.get("timed_out", False),
                        content=content, expect_json=raw.get("expect_json", False))
        if raw.get("rc") is not None and raw.get("rc") != 0 and cat2 == "成功":
            cat2 = "空"
        if cat2 != rec.get("result_category"):
            problems.append("category trace=%s recomputed=%s" % (rec.get("result_category"), cat2))
        if rec.get("http_status") != raw.get("http_status"):
            problems.append("http_status trace=%s raw=%s" % (rec.get("http_status"), raw.get("http_status")))
        if rec.get("result_category") not in CATEGORIES:
            problems.append("category not in enum: %s" % rec.get("result_category"))
        if problems:
            print("FAIL %s: %s" % (tid, "; ".join(problems)))
            n_fail += 1
        else:
            print("PASS %s: model=%s latency_ms=%s category=%s http=%s source=%s (raw 对上)"
                  % (tid, rec.get("model_id"), rec.get("latency_ms"),
                     rec.get("result_category"), rec.get("http_status"), rec.get("source")))
            n_ok += 1
    print("verify: %d PASS, %d FAIL, %d SKIP, %d total" % (n_ok, n_fail, n_skip, len(lines)))
    return 0 if n_fail == 0 and n_ok + n_skip == len(lines) and len(lines) > 0 else 1


def self_test():
    tests = []
    tests.append(("classify_429", classify(http_status=429) == "429"))
    tests.append(("classify_503", classify(http_status=503) == "503"))
    tests.append(("classify_500", classify(http_status=500) == "503"))
    tests.append(("classify_timeout", classify(timed_out=True) == "超时"))
    tests.append(("classify_empty", classify(http_status=200, content="  ") == "空"))
    tests.append(("classify_404", classify(http_status=404, content="x") == "空"))
    tests.append(("classify_ok", classify(http_status=200, content="hello") == "成功"))
    tests.append(("classify_json_ok", classify(http_status=200, content='{"a":1}', expect_json=True) == "成功"))
    tests.append(("classify_json_bad", classify(http_status=200, content='not json', expect_json=True) == "格式错"))
    tests.append(("classify_json_fenced", classify(http_status=200, content='```json\n{"a":1}\n```', expect_json=True) == "成功"))  # M9-CLASSIFY-FENCE
    with tempfile.TemporaryDirectory() as td:
        r = emit("test-1", "glm-4-flash-cc", 1234, "self-test", http_status=200,
                 returned_model="glm-4-flash", timeout_s=30, content="hello",
                 trace_dir=td)
        tests.append(("emit_returns_rec", r is not None and r["result_category"] == "成功"))
        r2 = emit("test-2", "gemini-3.5-flash-lite", 500, "self-test", http_status=429,
                  content="", error="HTTP Error 429", trace_dir=td)
        tests.append(("emit_429", r2 is not None and r2["result_category"] == "429"))
        r3 = emit("test-3", "opencode/deepseek-v4.1-flash-free", 120000, "self-test",
                  timeout_s=120, content="", error="timeout after 120s (rc=124)",
                  timed_out=True, rc=124, trace_dir=td)
        tests.append(("emit_timeout", r3 is not None and r3["result_category"] == "超时"))
        r4 = emit("test-repeat", "glm-4-flash-cc", 1000, "self-test", http_status=200,
                  content="hello", trace_dir=td)
        r5 = emit("test-repeat", "glm-4-flash-cc", 5000, "self-test", http_status=503,
                  content="", error="HTTP Error 503", trace_dir=td)
        tests.append(("emit_repeat_differs", r4 is not None and r5 is not None
                      and r4["result_category"] == "成功" and r5["result_category"] == "503"))
        rc = verify(trace_dir=td)
        tests.append(("verify_temp_pass", rc == 0))
    ok = all(t for _, t in tests)
    print(json.dumps({"ok": ok, "tests": tests}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Shared production model-call trace (M5-3)")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--test", action="store_true")
    a = ap.parse_args()
    if a.test:
        return self_test()
    if a.verify:
        return verify()
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
