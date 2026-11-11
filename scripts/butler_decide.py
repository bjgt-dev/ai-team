#!/usr/bin/env python3
"""Butler 24h role: rules engine first + bounded LLM decision runner.

Chairman order CHAIR-20261102-04. Role responsibility unchanged (judge /
decide / accept only); bearer changed from seat session to this script.
Safety rules (hard):
- Rules decide whenever they can; model is called only when rules cannot.
- The model NEVER writes state directly. This script validates the model
  output against a strict schema and only then writes the ledger/entry.
- No lock is held across a model call. No infinite retry.
- BUTLER_API switch: 0 = rules-only (model path refused, falls back to
  rules + alert). 1 = model path enabled (after probe gate, see below).
- Model key is NEVER read from files or printed. Real gateway calls use
  BUTLER_GATEWAY_KEY / BUTLER_GATEWAY_URL from the environment; tests use
  BUTLER_MODEL_MOCK=ok|fail. Without a key and without mock, model calls
  fail closed to the failure chain (never to a fabricated verdict).
"""
import hashlib
import json
import os
import re
import sys
import time
import urllib.request

CONF_PATH = "/home/ubuntu/ai-team/state/butler_api.conf"
CTX_CHAR_LIMIT = 12000  # approx <=4k tokens, truncation flagged
MODEL_TIMEOUT = 120
SHA64 = re.compile(r"^[0-9a-fA-F]{64}$")
VERDICTS = {"DONE", "FAILED", "DEAD", "NO_CHANGE", "DECIDE"}
LEVELS = {"L0", "L1", "L2"}
LANE_ORDER = {"P0": 0, "biz": 1, "infra": 2}


def read_switch():
    try:
        for line in open(CONF_PATH):
            line = line.strip()
            if line.startswith("BUTLER_API="):
                return line.split("=", 1)[1].strip()
    except OSError:
        return "0"
    return "0"


def priority_key(lane):
    return LANE_ORDER.get(lane, 9)


def receipt_ok(receipt):
    if not isinstance(receipt, dict):
        return False
    if not receipt.get("token"):
        return False
    sha = receipt.get("sha")
    if not sha or not SHA64.match(str(sha)):
        return False
    b = receipt.get("bytes")
    if not isinstance(b, int) or b <= 0:
        return False
    return True


def decide_rules(task):
    """Pure rules engine. Returns (verdict, level, conditions, rationale)."""
    if task.get("timeout_expired"):
        return ("NO_CHANGE", "L0", ["timeout: default_on_timeout=no-change"],
                "Timeout expired; default action is no change.")
    receipt = task.get("receipt")
    if task.get("verifier_pass") and receipt_ok(receipt):
        return ("DONE", "L0", ["verifier_pass", "receipt_complete"],
                "Verifier passed and receipt has token, 64-hex sha, bytes>0.")
    if not receipt_ok(receipt):
        if int(task.get("retry_count", 0)) >= int(task.get("max_attempts", 3)):
            return ("DEAD", "L1", ["receipt_invalid", "max_attempts_reached"],
                    "Receipt invalid and retries exhausted; dead with one summary alert.")
        return ("FAILED", "L1", ["receipt_invalid", "retry_with_backoff"],
                "Receipt invalid; failed with backoff retry.")
    return ("NO_CHANGE", "L0", ["no_rule_matched"],
            "No rule matched; default action is no change.")


def build_context_pack(task, auth_snippet=""):
    raw = json.dumps({"task": task, "auth": auth_snippet},
                     ensure_ascii=False, sort_keys=True)
    truncated = len(raw) > CTX_CHAR_LIMIT
    pack = raw[:CTX_CHAR_LIMIT] if truncated else raw
    sha = hashlib.sha256(pack.encode()).hexdigest()
    return pack, sha, truncated


def validate_model_output(obj):
    """Strict schema check. Returns (ok, reason)."""
    if not isinstance(obj, dict):
        return False, "not an object"
    if obj.get("verdict") not in VERDICTS:
        return False, "verdict not in enum"
    if obj.get("level") not in LEVELS:
        return False, "level not in L0-L2"
    if not isinstance(obj.get("conditions"), list):
        return False, "conditions not a list"
    if not obj.get("rationale") or not isinstance(obj.get("rationale"), str):
        return False, "rationale empty"
    return True, ""


FENCE = chr(96) * 3
_LAST_HTTP = None  # last real gateway HTTP status seen by _model_call_raw


def _model_call_raw(pack, model_id):
    """One model call. Mock for tests; real gateway via env key only."""
    global _LAST_HTTP
    mock = os.environ.get("BUTLER_MODEL_MOCK", "")
    if mock == "ok":
        _LAST_HTTP = 200
        return {"verdict": "DECIDE", "level": "L2",
                "conditions": ["mock-condition"],
                "rationale": "mock model decision ok"}, model_id
    if mock == "fail":
        raise RuntimeError("mock model failure")
    url = os.environ.get("BUTLER_GATEWAY_URL", "")
    key = os.environ.get("BUTLER_GATEWAY_KEY", "")
    if not url or not key:
        raise RuntimeError("no gateway configured (env key/url missing)")
    body = json.dumps({
        "model": model_id,
        "messages": [{"role": "user", "content":
                      "Output ONLY a JSON object, no markdown fences, no prose. Decide strictly in JSON {verdict, level, conditions, rationale}. "
                      "verdict in DONE/FAILED/DEAD/NO_CHANGE/DECIDE; level in L0/L1/L2; conditions must be a JSON array of strings.\n" + pack}],
        "temperature": 0}).encode()
    req = urllib.request.Request(url + "/chat/completions", data=body,
                                 headers={"Authorization": "Bearer " + key,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=MODEL_TIMEOUT) as resp:
        _LAST_HTTP = resp.status
        data = json.load(resp)
    text = data["choices"][0]["message"]["content"]
    t = text.strip()
    if t.startswith(FENCE):
        lines = t.splitlines()
        if lines and lines[0].startswith(FENCE):
            lines = lines[1:]
        if lines and lines[-1].strip() == FENCE:
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return json.loads(t), model_id

def model_call(pack, model_id, trace_task_id=None):
    # M5-3-IMPORT: trace module loaded by absolute path (importlib); regular import is blind to it on this hub, see patch_trace_import.py
    """Wrapper around _model_call_raw: same behavior + M5-3 trace emit (never breaks the call)."""
    import urllib.error as _urlerr
    _t0 = time.monotonic()
    _http = None; _err = ""; _to = False; _content = ""; _cat = None; _ok = False
    try:
        out, used = _model_call_raw(pack, model_id)
        _content = json.dumps(out, ensure_ascii=False)[:500]
        _ok = True
        return out, used
    except _urlerr.HTTPError as e:
        _http = e.code; _err = "HTTP Error %s" % e.code
        raise
    except Exception as e:
        _err = "%s: %s" % (type(e).__name__, str(e)[:200])
        _to = "timed out" in str(e).lower() or isinstance(e, TimeoutError)
        if isinstance(e, json.JSONDecodeError):
            _cat = "格式错"
        raise
    finally:
        try:
            if _ok:
                _http = globals().get("_LAST_HTTP")
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location("trace_20261105", "/home/ubuntu/ai-team/scripts/trace-20261105.py")
            _trace = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_trace)
            _tid = "butler-%s-%s-%d" % ((trace_task_id or "unknown"), model_id, int(_t0))
            _trace.emit(task_id=_tid, model_id=model_id,
                        latency_ms=int(round((time.monotonic() - _t0) * 1000)),
                        source="butler-model-mock" if os.environ.get("BUTLER_MODEL_MOCK") else "butler-decide",
                        result_category=_cat, http_status=_http,
                        returned_model=model_id, timeout_s=MODEL_TIMEOUT,
                        content=_content, error=_err, timed_out=_to)
        except Exception:
            pass


def probe_gate(primary, fallback):
    """Decision-level enable gate: two consecutive full calls + schema pass."""
    for _ in range(2):
        try:
            out, _ = model_call("PROBE", primary, trace_task_id="probe")
        except Exception:
            return False
        ok, _ = validate_model_output(out)
        if not ok:
            return False
    return True


def write_ledger(ledger_path, record):
    with open(ledger_path, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def write_summary_alert(alert_path, record):
    with open(alert_path, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def run(task, ledger_path, alert_path, auth_snippet="",
        primary="primary", fallback="fallback"):
    """Returns the ledger record. Never raises for a task-level failure;
    failures become NO_CHANGE + summary alert (fail closed)."""
    pack, ctx_sha, truncated = build_context_pack(task, auth_snippet)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    base = {"ts": ts, "task_id": task.get("task_id"),
            "ctx_sha": ctx_sha, "ctx_truncated": truncated}
    verdict, level, conditions, rationale = decide_rules(task)
    if verdict != "NO_CHANGE":
        rec = dict(base, path="rules", model_id=None, verdict=verdict,
                   level=level, conditions=conditions, rationale=rationale)
        write_ledger(ledger_path, rec)
        return rec
    if not task.get("needs_decision"):
        rec = dict(base, path="rules", model_id=None, verdict="NO_CHANGE",
                   level="L0", conditions=["no_rule_matched"],
                   rationale="No rule matched; default action is no change.")
        write_ledger(ledger_path, rec)
        return rec
    # Rules could not decide and the task explicitly needs a decision:
    # model path below (switch-gated, probe-gated, failure-chained).
    if read_switch() != "1":
        rec = dict(base, path="rules_only_fallback", model_id=None,
                   verdict="NO_CHANGE", level="L0",
                   conditions=["butler_api_off"],
                   rationale="BUTLER_API != 1; model path refused, rules only.")
        write_ledger(ledger_path, rec)
        return rec
    if not probe_gate(primary, fallback):
        rec = dict(base, path="rules_only_fallback", model_id=None,
                   verdict="NO_CHANGE", level="L0",
                   conditions=["probe_gate_failed"],
                   rationale="Probe gate failed; rules only, no fabricated decision.")
        write_ledger(ledger_path, rec)
        write_summary_alert(alert_path, dict(base, alert="probe_gate_failed"))
        return rec
    last_err = ""
    for model_id in (primary, fallback):
        try:
            out, used = model_call(pack, model_id, trace_task_id=task.get("task_id"))
        except Exception as e:
            last_err = str(e)
            continue
        ok, reason = validate_model_output(out)
        if not ok:
            last_err = "schema invalid: " + reason
            continue
        rec = dict(base, path="model", model_id=used, verdict=out["verdict"],
                   level=out["level"], conditions=out["conditions"],
                   rationale=out["rationale"])
        write_ledger(ledger_path, rec)
        return rec
    rec = dict(base, path="model_failed_default", model_id=None,
               verdict="NO_CHANGE", level="L0",
               conditions=["model_chain_exhausted"],
               rationale="Model chain exhausted (" + last_err + "); default no change.")
    write_ledger(ledger_path, rec)
    write_summary_alert(alert_path, dict(base, alert="model_chain_exhausted",
                                         detail=last_err))
    return rec


def main(argv):
    if len(argv) < 4:
        print("usage: butler_decide.py <task.json> <ledger.jsonl> <alerts.jsonl> "
              "[--needs-decision]", file=sys.stderr)
        return 2
    task = json.load(open(argv[1]))
    if "--needs-decision" in argv:
        task["needs_decision"] = True
    rec = run(task, argv[2], argv[3])
    print(json.dumps(rec, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
