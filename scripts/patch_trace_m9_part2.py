#!/usr/bin/env python3
"""patch_trace_m9_part2.py -- method 09: wire the remaining model-call sites.

Part 2 (anchors verified 2026-11-05 21:3x CST from hub reads):
  C. model-health.py -- probe_one (funnel for cmd_probe full 40-model probe
     and hourly cmd_probe_cooldown) + revive _ping (daily cmd_revive).
     _ping is lifted to module-level _revive_ping so one model can be
     probed directly for trace proof WITHOUT running the daily revive
     (which would move revive streaks). Behaviour otherwise identical.
  D. gateway_canary_tier2-20261104.py -- call() + 2 call sites (t1/t2,
     2 rounds). Same emit shape as gateway_canary; expect_json on t2.
  E. gateway_s2_medium-20261105.py -- call() + 3 call sites (a/b/c).
     Manual eval harness ("No production use" per its docstring); wired so
     any future run emits. expect_json on b.
  F. gateway_complex_code-20261105.py -- call() + 1 call site (code).
     Manual eval harness; wired the same way.
  G. gateway_canary-20261104.py -- 1-line tweak: t2 (JSON test) passes
     expect_json=(trace_label == "t2") so a non-JSON reply classifies as
     "格式错" instead of "成功". No other change to the M5-3 patch.

Safety: exact-anchor replaces (count must be 1); any mismatch aborts
BEFORE any write. Backups ".bak-20261105-m9trace" before writing. All
emits stay inside guarded try/except (trace can never break the caller).
All literals are single-quoted (no triple-quote trailing-"" trap; see
patch_trace_m9_part1.py repair note).
Usage: python3 scripts/patch_trace_m9_part2.py [--check-only]
Env override for dry-run testing: M9_HUB=/tmp/m9test2
"""
import os
import sys

HUB = os.environ.get("M9_HUB", "/home/ubuntu/ai-team")
BAK_SUFFIX = ".bak-20261105-m9trace"


def patch(path, pairs, marker):
    s = open(path, encoding="utf-8").read()
    if marker in s:
        print("already patched, skip: %s" % path)
        return False
    for old, new in pairs:
        if s.count(old) != 1:
            raise AssertionError("%s: anchor count %d (want 1): %r" % (path, s.count(old), old[:90]))
    if len(sys.argv) > 1 and sys.argv[1] == "--check-only":
        print("check ok: %s (%d anchors)" % (path, len(pairs)))
        return False
    bak = path + BAK_SUFFIX
    open(bak, "w", encoding="utf-8").write(s)
    for old, new in pairs:
        s = s.replace(old, new)
    open(path, "w", encoding="utf-8").write(s)
    print("patched: %s (backup %s)" % (path, bak))
    return True


PROBE_ONE_OLD = 'def probe_one(mid):\n    try:\n        d = http_json("POST", GATEWAY + "/chat/completions", {"model": mid, "messages": [{"role": "user", "content": "只回 OK"}], "max_tokens": 5}, timeout=30)\n        ch = (d.get("choices") or [])\n        if ch and (ch[0].get("message") or {}).get("content") is not None:\n            return True, ""\n        return False, "no choices"\n    except Exception as e:\n        return False, str(e)[:200]'

PROBE_ONE_NEW = 'def probe_one(mid):\n    # M9-TRACE-P2: one trace line per probe (never breaks probe).\n    _t0 = datetime.now()\n    _http = None; _err = ""; _to = False; _ok = False\n    try:\n        d = http_json("POST", GATEWAY + "/chat/completions", {"model": mid, "messages": [{"role": "user", "content": "只回 OK"}], "max_tokens": 5}, timeout=30)\n        ch = (d.get("choices") or [])\n        if ch and (ch[0].get("message") or {}).get("content") is not None:\n            _ok = True\n        else:\n            _err = "no choices"\n        _http = 200\n    except Exception as e:\n        _http = getattr(e, "code", None)\n        _err = str(e)[:200]\n        _to = "timed out" in _err.lower()\n    try:\n        import sys as _sys\n        _sys.path.insert(0, "/home/ubuntu/ai-team/scripts")\n        import trace_20261105 as _trace\n        _trace.emit(task_id="modelhealth-probe-" + mid.replace("/", "-").replace(":", "-") + "-" + datetime.now().strftime("%Y%m%d-%H%M%S"),\n                    model_id=mid, latency_ms=int(round((datetime.now() - _t0).total_seconds() * 1000)),\n                    source="model-health-probe", http_status=_http,\n                    returned_model="", timeout_s=30, content="OK" if _ok else "",\n                    error=_err, timed_out=_to)\n    except Exception:\n        pass\n    return _ok, _err'

PING_OLD = '    def _ping(mid):\n        try:\n            dd = http_json("POST", GATEWAY + "/chat/completions",\n                           {"model": mid, "messages": [{"role": "user", "content": "1+1=? 只回数字"}], "max_tokens": 5}, timeout=20)\n            ch = (dd.get("choices") or [])\n            txt = ((ch[0].get("message") or {}).get("content") or "").strip()\n            return mid, ("2" in txt)\n        except Exception:\n            return mid, False'

PING_NEW = '    def _ping(mid):\n        return mid, _revive_ping(mid)'

REVIVE_FN = 'def _revive_ping(mid):\n    # Module-level (was nested in cmd_revive) so one model can be probed\n    # directly for trace proof without running the daily revive (which would\n    # move revive streaks). Same logic as the old nested _ping + M9-TRACE-P2 emit.\n    _t0 = datetime.now()\n    _http = 200; _err = ""; _to = False; _txt = ""; _ok = False\n    try:\n        dd = http_json("POST", GATEWAY + "/chat/completions",\n                       {"model": mid, "messages": [{"role": "user", "content": "1+1=? 只回数字"}], "max_tokens": 5}, timeout=20)\n        ch = (dd.get("choices") or [])\n        _txt = ((ch[0].get("message") or {}).get("content") or "").strip()\n        _ok = ("2" in _txt)\n    except Exception as _e:\n        _http = getattr(_e, "code", None)\n        _err = str(_e)[:200]\n        _to = "timed out" in _err.lower()\n    try:\n        import sys as _sys\n        _sys.path.insert(0, "/home/ubuntu/ai-team/scripts")\n        import trace_20261105 as _trace\n        _trace.emit(task_id="modelhealth-revive-" + mid.replace("/", "-").replace(":", "-") + "-" + datetime.now().strftime("%Y%m%d-%H%M%S"),\n                    model_id=mid, latency_ms=int(round((datetime.now() - _t0).total_seconds() * 1000)),\n                    source="model-health-revive", http_status=_http,\n                    returned_model="", timeout_s=20, content=_txt if _ok else "",\n                    error=_err, timed_out=_to)\n    except Exception:\n        pass\n    return _ok\n\n\ndef cmd_revive(a):'

TIER2_CALL_OLD = 'def call(mid, content, max_tokens, timeout):\n    t0 = time.time()\n    body = json.dumps({"model": mid, "messages": [{"role": "user", "content": content}],\n                       "max_tokens": max_tokens, "temperature": 0}).encode()\n    req = urllib.request.Request(GATEWAY + "/chat/completions", data=body,\n        headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json"})\n    try:\n        with urllib.request.urlopen(req, timeout=timeout) as r:\n            d = json.loads(r.read().decode() or "{}")\n        ch = (d.get("choices") or [])\n        msg = (ch[0].get("message") or {}) if ch else {}\n        return (msg.get("content") or ""), d.get("model", ""), "", time.time() - t0\n    except Exception as e:\n        return "", "", str(e)[:200], time.time() - t0'

TIER2_CALL_NEW = 'def call(mid, content, max_tokens, timeout, trace_label=None, trace_round=None):\n    t0 = time.time()\n    body = json.dumps({"model": mid, "messages": [{"role": "user", "content": content}],\n                       "max_tokens": max_tokens, "temperature": 0}).encode()\n    req = urllib.request.Request(GATEWAY + "/chat/completions", data=body,\n        headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json"})\n    http_status = None\n    try:\n        with urllib.request.urlopen(req, timeout=timeout) as r:\n            http_status = r.status\n            d = json.loads(r.read().decode() or "{}")\n        ch = (d.get("choices") or [])\n        msg = (ch[0].get("message") or {}) if ch else {}\n        content_out, returned, error = (msg.get("content") or ""), d.get("model", ""), ""\n    except Exception as e:\n        http_status = getattr(e, "code", None)\n        content_out, returned, error = "", "", str(e)[:200]\n    elapsed = time.time() - t0\n    # M9-TRACE-P2: one line per model call (never breaks canary).\n    try:\n        if trace_label and trace_round:\n            import sys as _sys\n            _sys.path.insert(0, "/home/ubuntu/ai-team/scripts")\n            import trace_20261105 as _trace\n            _trace.emit(task_id="gwcanary-tier2-%s-%s-%s" % (trace_round, mid.replace("/", "-").replace(":", "-"), trace_label),\n                        model_id=mid, latency_ms=int(round(elapsed * 1000)),\n                        source="gateway-canary-tier2", http_status=http_status,\n                        returned_model=returned, timeout_s=timeout,\n                        content=content_out, error=error,\n                        timed_out=("timed out" in error.lower()),\n                        expect_json=(trace_label == "t2"))\n    except Exception:\n        pass\n    return content_out, returned, error, elapsed'

S2_CALL_OLD = 'def call(mid, content, max_tokens, timeout):\n    t0 = time.time()\n    body = json.dumps({"model": mid, "messages": [{"role": "user", "content": content}],\n                       "max_tokens": max_tokens, "temperature": 0}).encode()\n    req = urllib.request.Request(GATEWAY + "/chat/completions", data=body,\n        headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json"})\n    try:\n        with urllib.request.urlopen(req, timeout=timeout) as r:\n            d = json.loads(r.read().decode() or "{}")\n        ch = (d.get("choices") or [])\n        return ((ch[0].get("message") or {}).get("content") or ""), "", time.time() - t0\n    except Exception as e:\n        return "", str(e)[:200], time.time() - t0'

S2_CALL_NEW = 'def call(mid, content, max_tokens, timeout, trace_label=None):\n    t0 = time.time()\n    body = json.dumps({"model": mid, "messages": [{"role": "user", "content": content}],\n                       "max_tokens": max_tokens, "temperature": 0}).encode()\n    req = urllib.request.Request(GATEWAY + "/chat/completions", data=body,\n        headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json"})\n    http_status = None\n    try:\n        with urllib.request.urlopen(req, timeout=timeout) as r:\n            http_status = r.status\n            d = json.loads(r.read().decode() or "{}")\n        ch = (d.get("choices") or [])\n        content_out = (ch[0].get("message") or {}).get("content") or ""\n        returned = d.get("model", "")\n        error = ""\n    except Exception as e:\n        http_status = getattr(e, "code", None)\n        content_out, returned, error = "", "", str(e)[:200]\n    elapsed = time.time() - t0\n    # M9-TRACE-P2: one line per model call (never breaks eval).\n    try:\n        if trace_label:\n            import sys as _sys\n            _sys.path.insert(0, "/home/ubuntu/ai-team/scripts")\n            import trace_20261105 as _trace\n            _trace.emit(task_id="s2-%s-%s-%s" % (mid.replace("/", "-").replace(":", "-"), trace_label, time.strftime("%Y%m%d-%H%M%S")),\n                        model_id=mid, latency_ms=int(round(elapsed * 1000)),\n                        source="gateway-s2-medium", http_status=http_status,\n                        returned_model=returned, timeout_s=timeout,\n                        content=content_out, error=error,\n                        timed_out=("timed out" in error.lower()),\n                        expect_json=(trace_label == "b"))\n    except Exception:\n        pass\n    return content_out, error, elapsed'

COMPLEX_CALL_NEW = 'def call(mid, content, max_tokens, timeout, trace_label=None):\n    t0 = time.time()\n    body = json.dumps({"model": mid, "messages": [{"role": "user", "content": content}],\n                       "max_tokens": max_tokens, "temperature": 0}).encode()\n    req = urllib.request.Request(GATEWAY + "/chat/completions", data=body,\n        headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json"})\n    http_status = None\n    try:\n        with urllib.request.urlopen(req, timeout=timeout) as r:\n            http_status = r.status\n            d = json.loads(r.read().decode() or "{}")\n        ch = (d.get("choices") or [])\n        content_out = (ch[0].get("message") or {}).get("content") or ""\n        returned = d.get("model", "")\n        error = ""\n    except Exception as e:\n        http_status = getattr(e, "code", None)\n        content_out, returned, error = "", "", str(e)[:200]\n    elapsed = time.time() - t0\n    # M9-TRACE-P2: one line per model call (never breaks eval).\n    try:\n        if trace_label:\n            import sys as _sys\n            _sys.path.insert(0, "/home/ubuntu/ai-team/scripts")\n            import trace_20261105 as _trace\n            _trace.emit(task_id="complex-%s-%s-%s" % (mid.replace("/", "-").replace(":", "-"), trace_label, time.strftime("%Y%m%d-%H%M%S")),\n                        model_id=mid, latency_ms=int(round(elapsed * 1000)),\n                        source="gateway-complex-code", http_status=http_status,\n                        returned_model=returned, timeout_s=timeout,\n                        content=content_out, error=error,\n                        timed_out=("timed out" in error.lower()))\n    except Exception:\n        pass\n    return content_out, error, elapsed'


def main():
    # ---- C. model-health.py ----
    patch(HUB + "/scripts/model-health.py", [
        (PROBE_ONE_OLD, PROBE_ONE_NEW),
        (PING_OLD, PING_NEW),
        ('def cmd_revive(a):', REVIVE_FN),
    ], marker="M9-TRACE-P2")

    # ---- D. gateway_canary_tier2 ----
    patch(HUB + "/scripts/gateway_canary_tier2-20261104.py", [
        (TIER2_CALL_OLD, TIER2_CALL_NEW),
        ('call(mid, "只回复口令：" + tok, 16, 130)',
         'call(mid, "只回复口令：" + tok, 16, 130, trace_label="t1", trace_round=a.round + "-r%d" % rnd)'),
        ('call(mid, "只输出JSON，不要其他文字，含3个键：task、result、ok，值自定", 128, 130)',
         'call(mid, "只输出JSON，不要其他文字，含3个键：task、result、ok，值自定", 128, 130, trace_label="t2", trace_round=a.round + "-r%d" % rnd)'),
    ], marker="M9-TRACE-P2")

    # ---- E. gateway_s2_medium ----
    patch(HUB + "/scripts/gateway_s2_medium-20261105.py", [
        (S2_CALL_OLD, S2_CALL_NEW),
        ('DOC_A, 300, 60)', 'DOC_A, 300, 60, trace_label="a")'),
        ('MEMO_B, 300, 60)', 'MEMO_B, 300, 60, trace_label="b")'),
        ('REPORT_C, 500, 90)', 'REPORT_C, 500, 90, trace_label="c")'),
    ], marker="M9-TRACE-P2")

    # ---- F. gateway_complex_code (same old call() shape as S2) ----
    patch(HUB + "/scripts/gateway_complex_code-20261105.py", [
        (S2_CALL_OLD, COMPLEX_CALL_NEW),
        ('call(mid, PROMPT, 800, 120)', 'call(mid, PROMPT, 800, 120, trace_label="code")'),
    ], marker="M9-TRACE-P2")

    # ---- G. gateway_canary t2 expect_json ----
    patch(HUB + "/scripts/gateway_canary-20261104.py", [
        ('                        content=content_out, error=error,\n                        timed_out=("timed out" in error.lower()))',
         '                        content=content_out, error=error,\n                        timed_out=("timed out" in error.lower()),\n                        expect_json=(trace_label == "t2"))  # M9-TRACE-P2'),
    ], marker="M9-TRACE-P2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
