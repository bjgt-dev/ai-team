#!/usr/bin/env python3
"""c2-review-hook-20261105.py -- cross-supplier review hook for c2-verifier (M5-2, CHAIR-20261105-18 residual 2).

M3 exit item 2 proved review only as MANUAL ordered steps (runner: executor ->
review -> verifier -> DONE); the review was NOT an automatic hook inside
c2-verifier, and automatic enforcement for all deliverable tasks was an open
follow-up (see state/m3-exit-20261105/2-review-receipt.json scope_note).
This script closes it: c2-verifier calls it automatically for every llm_doc
task whose contract sets review_required=true.

Rules (hard):
  - Reviewer MUST be a different supplier than the producer (checked with
    scripts/review-supplier-check.py; same/unknown supplier => ERROR, no verdict).
  - Supported producer/reviewer pairs (LIGHT main pool only, per L4 routing
    decisions 2026-11-05 -- glm-4-flash-cc and gemini-3.5-flash-lite are the two
    LIGHT mainstays, 5:5 rotation; reuse that data, do not invent new pairs):
      producer glm-4-flash-cc        -> reviewer gemini-3.5-flash-lite
      producer gemini-3.5-flash-lite -> reviewer glm-4-flash-cc
    Any other producer_model => ERROR (fail closed, never auto-pick a reviewer).
  - Review can NEVER turn a deterministic failure into DONE: the hook only runs
    after c2-verifier's deterministic checks already passed, and its verdict is
    one input to the verifier (PASS => DONE, FAIL => RETRY_WAIT, ERROR => the
    task stays VERIFYING for the next minute's retry; see c2-verifier).
  - No DB writes by this script. It only reads the task row + deliverable file
    and writes review records under state/m5-20261105/review/.
  - No spend beyond the free gateway pool; one reviewer call, max_tokens 512,
    timeout 60s.

Usage:
  python3 scripts/c2-review-hook-20261105.py --task <uuid>
      exit 0 = REVIEW PASS, 1 = REVIEW FAIL, 2 = ERROR (no verdict)
  python3 scripts/c2-review-hook-20261105.py --test
      logic tests only (supplier mapping, merge rules, JSON-parse fallback);
      no gateway call, no DB access.

Method-07 extension (2026-11-05, state/method-07-review/):
  contract.review_strength selects the review strength by deliverable
  criticality ("medium" default, "strong" for critical code/plans):
  - medium: one LIGHT cross-supplier reviewer call (M5-2 behaviour unchanged),
    max_tokens 512, timeout 60s. Ordinary docs.
  - strong: TWO independent reviewer calls from TWO different suppliers, both
    different from the producer supplier; BOTH must PASS. Primary = the LIGHT
    cross-supplier reviewer; second = nvidia/nemotron-3-ultra-550b-a55b:free
    (OpenRouter:nvidia, third supplier; runnable-code evidence in
    state/gateway-layers-20261105/complex-code-20261105.json). Each call
    max_tokens 1024, timeout 120s. Either FAIL => FAIL; either ERROR => ERROR
    (fail closed, never silently downgrades to medium). Per-reviewer records
    are written as strong-<task>-<tag>.json (a prefix the verifier's
    attempt counter does NOT match); the combined verdict is one attempt
    via the normal review-<task>-<n>.json record.
  Unknown review_strength => ERROR (fail closed).
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "/home/ubuntu/ai-team"
DSN_FILE = BASE + "/state/.cq-dsn"
DELIV_DIR = BASE + "/state/deliverables"
REVIEW_DIR = BASE + "/state/m5-20261105/review"
SUPPLIER_CHECK = BASE + "/scripts/review-supplier-check.py"
OPENCODE_CFG = "/home/ubuntu/.config/opencode/opencode.json"
GATEWAY = "http://127.0.0.1:3001/v1"

REVIEWER_FOR = {
    "glm-4-flash-cc": "gemini-3.5-flash-lite",
    "gemini-3.5-flash-lite": "glm-4-flash-cc",
}

# Method-07 strong second reviewer: a third supplier (OpenRouter:nvidia),
# different from both Zhipu and Google producers. Only used for
# review_strength=strong; never auto-picked for medium.
STRONG_SECOND_REVIEWER = "nvidia/nemotron-3-ultra-550b-a55b:free"

REVIEW_STRENGTHS = ("medium", "strong")


def strong_reviewers_for(producer):
    """[primary LIGHT reviewer, STRONG_SECOND_REVIEWER] for a producer."""
    return [REVIEWER_FOR[producer], STRONG_SECOND_REVIEWER]


def combine_verdicts(verdicts):
    """Strong verdict: all PASS => PASS; any FAIL => FAIL; else ERROR."""
    if not verdicts:
        return "ERROR"
    if any(v == "ERROR" for v in verdicts):
        return "ERROR"
    if any(v == "FAIL" for v in verdicts):
        return "FAIL"
    if all(v == "PASS" for v in verdicts):
        return "PASS"
    return "ERROR"


def get_dsn():
    try:
        with open(DSN_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("CQ_DSN="):
                    return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def gateway_token():
    c = json.load(open(OPENCODE_CFG))
    for p in c.get("provider", {}).values():
        opt = p.get("options") or {}
        if opt.get("apiKey"):
            return opt["apiKey"]
    return ""


def load_task(task_id):
    dsn = get_dsn()
    if not dsn:
        raise RuntimeError("no DSN")
    import psycopg
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT payload, artifact_sha, artifact_bytes, status FROM central_tasks WHERE task_id=%s", (task_id,))
            row = cur.fetchone()
    if not row:
        raise RuntimeError("task not found: %s" % task_id)
    payload, sha, nbytes, status = row
    return {"payload": payload, "artifact_sha": sha, "artifact_bytes": nbytes, "status": status}


def read_text(path, limit):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()[:limit]
    except Exception:
        return ""


def review_call(reviewer_model, source_text, artifact_text, anchors, timeout_s=60, max_tokens=512):
    """One reviewer gateway call. Returns (rec_dict, error_str)."""
    t0 = time.monotonic()
    prompt = (
        "你是独立复核席。你正在复核一份内部交付文档，只给 JSON verdict，不写其他文字。\n"
        "核对清单（全部满足才判 PASS）：\n"
        "1) 交付稿完整保留了源稿的各部分，没有漏掉的部分；\n"
        "2) 没有编造数字、日期、价格、联系方式；价格与联系方式必须保持待定/待批状态；\n"
        "3) 没有新增保证转化、保证效果的承诺；\n"
        "4) 内容紧贴源稿，没有超出源稿范围的新主张。\n"
        "源稿要点（fidelity anchors）：%s\n\n"
        "--- 源稿 ---\n%s\n\n--- 交付稿 ---\n%s\n\n"
        "只输出一个 JSON 对象，不要 markdown fence，不要解释："
        "{\"verdict\": \"PASS\" 或 \"FAIL\", \"reasons\": [\"理由1\", \"理由2\", ...]}。"
        "reasons 必须至少 2 条，每条至少 10 个字，具体写明核对了哪一项、结果是什么；"
        "只给 verdict 不给理由、或 reasons 为空，视为本次复核无效。"
        % ("；".join(anchors) if anchors else "（无）",
           source_text[:4000], artifact_text[:6000]))
    body = json.dumps({"model": reviewer_model,
                       "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": max_tokens, "temperature": 0}).encode()
    req = urllib.request.Request(
        GATEWAY + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + gateway_token(),
                 "Content-Type": "application/json"})
    http_status, content, returned, error, timed_out = None, "", "", "", False
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            http_status = r.status
            d = json.loads(r.read().decode() or "{}")
        ch = (d.get("choices") or [])
        msg = (ch[0].get("message") or {}) if ch else {}
        content = msg.get("content") or ""
        returned = d.get("model", "") or ""
    except urllib.error.HTTPError as e:
        http_status = e.code
        error = "HTTP Error %s: %s" % (e.code, e.reason or "")
        try:
            content = e.read().decode()[:200]
        except Exception:
            pass
    except Exception as e:
        error = "%s: %s" % (type(e).__name__, str(e)[:200])
        timed_out = "timed out" in str(e).lower() or isinstance(e, TimeoutError)
    elapsed_ms = int(round((time.monotonic() - t0) * 1000))
    if timed_out:
        cat = "超时"
    elif http_status == 429:
        cat = "429"
    elif http_status is not None and http_status >= 500:
        cat = "503"
    elif not (content or "").strip():
        cat = "空"
    else:
        cat = "成功"
    return {"http_status": http_status, "returned_model": returned,
            "content": content, "error": error[:200], "timed_out": timed_out,
            "latency_ms": elapsed_ms, "error_category": cat}, ""


def parse_verdict(text):
    """Parse reviewer JSON. Returns (verdict, reasons, error_str)."""
    t = (text or "").strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    try:
        obj = json.loads(t)
    except Exception as exc:
        return None, [], "review reply not JSON: %s" % exc
    if not isinstance(obj, dict):
        return None, [], "review reply not an object"
    verdict = obj.get("verdict")
    if verdict not in ("PASS", "FAIL"):
        return None, [], "review verdict not PASS/FAIL: %r" % (verdict,)
    reasons = obj.get("reasons")
    if not isinstance(reasons, list) or not reasons or not all(isinstance(r, str) and r.strip() for r in reasons):
        return None, [], "review reasons missing/empty"
    return verdict, reasons, ""


def attempt_no(task_id):
    os.makedirs(REVIEW_DIR, exist_ok=True)
    n = 0
    for name in os.listdir(REVIEW_DIR):
        if name.startswith("review-%s-" % task_id) and name.endswith(".json"):
            n += 1
    return n + 1


def write_record(task_id, record):
    os.makedirs(REVIEW_DIR, exist_ok=True)
    n = attempt_no(task_id)
    record = dict(record, attempt=n)
    with open(os.path.join(REVIEW_DIR, "review-%s-%d.json" % (task_id, n)), "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2, sort_keys=True)
    with open(os.path.join(REVIEW_DIR, "review-%s.json" % task_id), "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2, sort_keys=True)
    return n


def run_review(task_id):
    """Returns exit code: 0 PASS, 1 FAIL, 2 ERROR. Always writes a record on ERROR/FAIL/PASS."""
    t_start = time.time()
    try:
        task = load_task(task_id)
    except Exception as exc:
        rec = {"task_id": str(task_id), "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "verdict": "ERROR", "error": "load task: %s" % exc}
        write_record(task_id, rec)
        print("REVIEW ERROR %s: %s" % (task_id, exc))
        return 2
    payload = task["payload"] or {}
    contract = payload.get("contract") if isinstance(payload, dict) else None
    if not isinstance(contract, dict):
        rec = {"task_id": str(task_id), "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "verdict": "ERROR", "error": "no contract in payload (review hook needs contract.producer_model)"}
        write_record(task_id, rec)
        print("REVIEW ERROR %s: no contract" % task_id)
        return 2
    producer = contract.get("producer_model")
    if not isinstance(producer, str) or producer not in REVIEWER_FOR:
        rec = {"task_id": str(task_id), "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "verdict": "ERROR", "error": "unsupported producer_model: %r (supported: %s)" % (producer, sorted(REVIEWER_FOR))}
        write_record(task_id, rec)
        print("REVIEW ERROR %s: unsupported producer_model %r" % (task_id, producer))
        return 2
    # Method-07: review strength by deliverable criticality. Default medium.
    strength = contract.get("review_strength", "medium")
    if strength not in REVIEW_STRENGTHS:
        rec = {"task_id": str(task_id), "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "verdict": "ERROR", "error": "unknown review_strength: %r (allowed: medium, strong)" % (strength,)}
        write_record(task_id, rec)
        print("REVIEW ERROR %s: unknown review_strength %r" % (task_id, strength))
        return 2
    reviewers = [REVIEWER_FOR[producer]] if strength == "medium" else strong_reviewers_for(producer)
    for reviewer in reviewers:
        try:
            r = subprocess.run([sys.executable, SUPPLIER_CHECK, producer, reviewer],
                               capture_output=True, text=True, timeout=30)
        except Exception as exc:
            rec = {"task_id": str(task_id), "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   "verdict": "ERROR", "error": "supplier check could not run: %s" % exc}
            write_record(task_id, rec)
            print("REVIEW ERROR %s: supplier check error" % task_id)
            return 2
        if r.returncode != 0:
            rec = {"task_id": str(task_id), "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   "verdict": "ERROR", "error": "supplier check FAILED for %s: %s" % (reviewer, ((r.stdout or "") + (r.stderr or ""))[:200])}
            write_record(task_id, rec)
            print("REVIEW ERROR %s: supplier check failed for %s" % (task_id, reviewer))
            return 2
    artifact_path = os.path.join(DELIV_DIR, "%s.md" % task_id)
    artifact_text = read_text(artifact_path, 6000)
    if not artifact_text.strip():
        rec = {"task_id": str(task_id), "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "verdict": "ERROR", "error": "deliverable file missing/empty: %s" % artifact_path,
               "producer_model": producer, "reviewer_model": reviewers[0]}
        write_record(task_id, rec)
        print("REVIEW ERROR %s: deliverable missing" % task_id)
        return 2
    source_file = contract.get("source_file")
    source_text = read_text(source_file, 4000) if isinstance(source_file, str) and source_file else ""
    anchors = contract.get("fidelity_anchors")
    if not isinstance(anchors, list):
        anchors = []
    max_tokens = 512 if strength == "medium" else 1024
    timeout_s = 60 if strength == "medium" else 120
    results = []
    for reviewer in reviewers:
        call, _ = review_call(reviewer, source_text, artifact_text, anchors,
                              timeout_s=timeout_s, max_tokens=max_tokens)
        base = {"task_id": str(task_id), "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "producer_model": producer, "reviewer_model": reviewer,
                "cross_supplier": True, "artifact_path": artifact_path,
                "artifact_sha": task["artifact_sha"], "artifact_bytes": task["artifact_bytes"],
                "reviewer_latency_ms": call["latency_ms"], "reviewer_http_status": call["http_status"],
                "reviewer_returned_model": call["returned_model"],
                "reviewer_error_category": call["error_category"],
                "reviewer_raw_snip": (call["content"] or "")[:300]}
        if call["error_category"] != "成功":
            results.append(dict(base, verdict="ERROR",
                                error="reviewer call %s: %s" % (call["error_category"], call["error"][:200])))
            continue
        verdict_i, reasons_i, perr = parse_verdict(call["content"])
        if perr:
            results.append(dict(base, verdict="ERROR", error="parse: %s" % perr))
            continue
        results.append(dict(base, verdict=verdict_i, reasons=reasons_i,
                            seconds=round(time.time() - t_start, 1)))
    if strength == "strong":
        # Per-reviewer sub-records. Prefix "strong-<task>-" is deliberately NOT
        # "review-<task>-", so the verifier's attempt counter does not count
        # sub-records as separate attempts (one hook run = one attempt).
        for sub in results:
            tag = sub["reviewer_model"].replace("/", "_").replace(":", "_").replace("-", "_")[:40]
            try:
                with open(os.path.join(REVIEW_DIR, "strong-%s-%s.json" % (task_id, tag)), "w", encoding="utf-8") as f:
                    json.dump(dict(sub, review_strength="strong"), f, ensure_ascii=False, indent=2, sort_keys=True)
            except Exception:
                pass
    verdict = combine_verdicts([r["verdict"] for r in results])
    reasons = []
    for r in results:
        reasons.extend(r.get("reasons") or [])
        if r["verdict"] == "ERROR":
            reasons.append("reviewer %s ERROR: %s" % (r["reviewer_model"], r.get("error", "")[:200]))
    primary = results[0]
    rec = dict(primary, verdict=verdict, reasons=reasons,
               review_strength=strength,
               reviewers=[{"reviewer_model": r["reviewer_model"], "verdict": r["verdict"],
                           "reasons": r.get("reasons") or [],
                           "reviewer_latency_ms": r["reviewer_latency_ms"],
                           "reviewer_http_status": r["reviewer_http_status"],
                           "reviewer_returned_model": r["reviewer_returned_model"],
                           "reviewer_error_category": r["reviewer_error_category"],
                           "error": r.get("error", "")} for r in results],
               seconds=round(time.time() - t_start, 1))
    write_record(task_id, rec)
    print("REVIEW %s %s strength=%s reviewers=%s reasons=%d" % (verdict, task_id, strength, ",".join(reviewers), len(reasons)))
    return 0 if verdict == "PASS" else 1


def self_test():
    tests = []
    tests.append(("reviewer_map_glm", REVIEWER_FOR.get("glm-4-flash-cc") == "gemini-3.5-flash-lite"))
    tests.append(("reviewer_map_gemini", REVIEWER_FOR.get("gemini-3.5-flash-lite") == "glm-4-flash-cc"))
    tests.append(("reviewer_map_unknown", "agnes-3.0-flash" not in REVIEWER_FOR))
    v, rs, e = parse_verdict('{"verdict": "PASS", "reasons": ["ok"]}')
    tests.append(("parse_plain", v == "PASS" and rs == ["ok"] and e == ""))
    v, rs, e = parse_verdict('```json\n{"verdict": "FAIL", "reasons": ["x"]}\n```')
    tests.append(("parse_fenced", v == "FAIL" and rs == ["x"]))
    v, rs, e = parse_verdict('not json')
    tests.append(("parse_bad_is_error", v is None and e != ""))
    v, rs, e = parse_verdict('{"verdict": "MAYBE", "reasons": ["x"]}')
    tests.append(("parse_bad_verdict_is_error", v is None))
    v, rs, e = parse_verdict('{"verdict": "PASS", "reasons": []}')
    tests.append(("parse_empty_reasons_is_error", v is None))
    # Method-07: strong mapping + combine logic (no gateway call).
    tests.append(("strong_glm_pair", strong_reviewers_for("glm-4-flash-cc") == ["gemini-3.5-flash-lite", STRONG_SECOND_REVIEWER]))
    tests.append(("strong_gemini_pair", strong_reviewers_for("gemini-3.5-flash-lite") == ["glm-4-flash-cc", STRONG_SECOND_REVIEWER]))
    tests.append(("strong_second_differs_from_both", STRONG_SECOND_REVIEWER not in ("glm-4-flash-cc", "gemini-3.5-flash-lite") and STRONG_SECOND_REVIEWER not in REVIEWER_FOR.values()))
    tests.append(("combine_pp", combine_verdicts(["PASS", "PASS"]) == "PASS"))
    tests.append(("combine_pf", combine_verdicts(["PASS", "FAIL"]) == "FAIL"))
    tests.append(("combine_pe", combine_verdicts(["PASS", "ERROR"]) == "ERROR"))
    tests.append(("combine_single_pass", combine_verdicts(["PASS"]) == "PASS"))
    tests.append(("combine_single_fail", combine_verdicts(["FAIL"]) == "FAIL"))
    tests.append(("combine_empty_is_error", combine_verdicts([]) == "ERROR"))
    tests.append(("strengths_known", set(REVIEW_STRENGTHS) == {"medium", "strong"}))
    ok = all(t for _, t in tests)
    print(json.dumps({"ok": ok, "tests": tests}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="c2-verifier cross-supplier review hook (M5-2)")
    ap.add_argument("--task", default=None)
    ap.add_argument("--test", action="store_true")
    a = ap.parse_args()
    if a.test:
        return self_test()
    if a.task:
        return run_review(a.task)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
