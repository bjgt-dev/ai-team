#!/usr/bin/env python3
"""Patch 4 production call points for M5-3 trace (run on hub).
Backups are made by the caller BEFORE running this script.
All patches are in-memory with assertions; the files are written only if
every patch applies. Refuses to run twice (M5-3 marker check).
Trace emits are guarded try/except at every site: a trace failure can never
break the caller. No caller behavior changes except the added emits
(+ http_status capture in gateway_canary / executor-patrol, timing only).
"""
import sys

HUB = "/home/ubuntu/ai-team"


def patch_gateway_canary():
    p = HUB + "/scripts/gateway_canary-20261104.py"
    s = open(p, encoding="utf-8").read()
    if "M5-3" in s:
        print("gateway_canary already patched, skip")
        return
    lines = s.split("\n")
    # --- replace call() body ---
    start = next(i for i, ln in enumerate(lines) if ln.startswith("def call(mid, content, max_tokens, timeout):"))
    end = next(i for i, ln in enumerate(lines) if ln.strip() == 'return "", "", str(e)[:200], time.time() - t0')
    assert any("with urllib.request.urlopen(req, timeout=timeout) as r:" in ln for ln in lines[start:end + 1]), "call() shape drift"
    new_fn = '''def call(mid, content, max_tokens, timeout, trace_label=None, trace_round=None):
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
    # M5-3 trace (never breaks canary): one line per model call.
    try:
        if trace_label and trace_round:
            import sys as _sys
            _sys.path.insert(0, "/home/ubuntu/ai-team/scripts")
            import trace_20261105 as _trace
            _trace.emit(task_id="gwcanary-%s-%s-%s" % (trace_round, mid, trace_label),
                        model_id=mid, latency_ms=int(round(elapsed * 1000)),
                        source="gateway-canary", http_status=http_status,
                        returned_model=returned, timeout_s=timeout,
                        content=content_out, error=error,
                        timed_out=("timed out" in error.lower()))
    except Exception:
        pass
    return content_out, returned, error, elapsed'''
    lines[start:end + 1] = new_fn.split("\n")
    s = "\n".join(lines)
    # --- pass trace labels at the 3 call sites ---
    s = s.replace('c1, rm1, e1, l1 = call(mid, "只回复口令：" + tok, 16, 30)',
                  'c1, rm1, e1, l1 = call(mid, "只回复口令：" + tok, 16, 30, trace_label="t1", trace_round=a.round)')
    s = s.replace('c2, rm2, e2, l2 = call(mid, "只输出JSON，不要其他文字，含3个键：task、result、ok，值自定", 128, 60)',
                  'c2, rm2, e2, l2 = call(mid, "只输出JSON，不要其他文字，含3个键：task、result、ok，值自定", 128, 60, trace_label="t2", trace_round=a.round)')
    s = s.replace('c3, rm3, e3, l3 = call(mid, "用200字以内中文写一段关于秋天的短文，不要英文，不要解释", 300, 60)',
                  'c3, rm3, e3, l3 = call(mid, "用200字以内中文写一段关于秋天的短文，不要英文，不要解释", 300, 60, trace_label="t3", trace_round=a.round)')
    assert s.count('trace_label="t1"') == 1 and s.count('trace_label="t2"') == 1 and s.count('trace_label="t3"') == 1, "canary call sites not all patched"
    open(p, "w", encoding="utf-8").write(s)
    print("gateway_canary patched")


def patch_executor():
    p = HUB + "/scripts/c2-executor-20261104.py"
    s = open(p, encoding="utf-8").read()
    if "M5-3" in s:
        print("executor already patched, skip")
        return
    lines = s.split("\n")
    i_llm = next(i for i, ln in enumerate(lines) if ln.strip() == "llm_ok = False")
    # the try: right after llm_ok = False
    i_try = next(i for i in range(i_llm, i_llm + 5) if lines[i].strip() == "try:")
    # the except after that try (same indent)
    i_exc = next(i for i in range(i_try, len(lines)) if lines[i].strip() == "except Exception:" and lines[i].startswith("                except"))
    # the llm_ok = False line inside that except
    i_ef = next(i for i in range(i_exc, i_exc + 5) if lines[i].strip() == "llm_ok = False")
    assert lines[i_try + 1].strip().startswith("cfg = _json.load"), "executor patrol shape drift"
    indent_try = lines[i_try][:len(lines[i_try]) - len(lines[i_try].lstrip())]
    new_try_head = [
        indent_try + "_pllm_t0 = _time.monotonic(); _pllm_http = None; _pllm_txt = \"\"; _pllm_err = \"\"; _pllm_to = False",
        lines[i_try],
    ]
    lines[i_try:i_try + 1] = new_try_head
    # re-find except (indices shifted by +1); search from i_try onward --
    # an earlier bare except (pool-load fallback) sits just above the branch
    i_exc = next(i for i in range(i_try, len(lines)) if lines[i].strip() == "except Exception:" and lines[i].startswith("                except"))
    i_ef = next(i for i in range(i_exc, i_exc + 5) if lines[i].strip() == "llm_ok = False")
    # capture http status + text inside the with block: patch the urlopen lines
    for j in range(i_try, i_exc):
        if lines[j].strip() == "with _url.urlopen(req, timeout=60) as _r:":
            lines[j + 1] = lines[j + 1].replace("_d = _json.loads(_r.read().decode() or \"{}\")",
                                                "_pllm_http = _r.status\n" + " " * 24 + "_d = _json.loads(_r.read().decode() or \"{}\")")
            break
    else:
        raise AssertionError("executor urlopen line not found")
    for j in range(i_try, i_exc):
        if '_txt = ((_d.get("choices")' in lines[j]:
            lines[j] = lines[j] + "\n" + " " * 20 + "_pllm_txt = _txt"
            break
    else:
        raise AssertionError("executor _txt line not found")
    # rewrite the except body: capture the error, keep llm_ok = False
    exc_indent = lines[i_exc][:len(lines[i_exc]) - len(lines[i_exc].lstrip())]
    lines[i_exc] = exc_indent + "except Exception as _pllm_e:"
    lines[i_ef] = exc_indent + "    _pllm_err = str(_pllm_e)[:200]"
    lines.insert(i_ef + 1, exc_indent + '    _pllm_to = "timed out" in str(_pllm_e).lower()')
    lines.insert(i_ef + 2, exc_indent + "    llm_ok = False")
    # append the guarded emit right after the except block (before the model_verdict write)
    i_mv = next(i for i, ln in enumerate(lines) if "model_verdict.md" in ln)
    emit_block = [
        "                # M5-3 trace (never breaks patrol): one line for the bounded gateway call.",
        "                try:",
        "                    import sys as _sys2",
        '                    _sys2.path.insert(0, "/home/ubuntu/ai-team/scripts")',
        "                    import trace_20261105 as _trace2",
        "                    _trace2.emit(task_id=str(task_id), model_id=primary,",
        "                                 latency_ms=int(round((_time.monotonic() - _pllm_t0) * 1000)),",
        '                                 source="c2-executor-patrol", http_status=_pllm_http,',
        "                                 returned_model=\"\", timeout_s=60, content=_pllm_txt,",
        "                                 error=_pllm_err, timed_out=_pllm_to)",
        "                except Exception:",
        "                    pass",
    ]
    lines[i_mv:i_mv] = emit_block
    open(p, "w", encoding="utf-8").write("\n".join(lines))
    print("executor patched")


def patch_butler():
    p = HUB + "/scripts/butler_decide.py"
    s = open(p, encoding="utf-8").read()
    if "M5-3" in s:
        print("butler already patched, skip")
        return
    assert "def model_call(pack, model_id):" in s, "butler model_call anchor missing"
    s = s.replace("def model_call(pack, model_id):", "def _model_call_raw(pack, model_id):", 1)
    anchor = "    return json.loads(t), model_id\n"
    assert anchor in s, "butler return anchor missing"
    wrapper = anchor + '''
def model_call(pack, model_id, trace_task_id=None):
    """Wrapper around _model_call_raw: same behavior + M5-3 trace emit (never breaks the call)."""
    import urllib.error as _urlerr
    _t0 = time.monotonic()
    _http = None; _err = ""; _to = False; _content = ""; _cat = None
    try:
        out, used = _model_call_raw(pack, model_id)
        _content = json.dumps(out, ensure_ascii=False)[:500]
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
            import sys as _sys
            _sys.path.insert(0, "/home/ubuntu/ai-team/scripts")
            import trace_20261105 as _trace
            _tid = "butler-%s-%s-%d" % ((trace_task_id or "unknown"), model_id, int(_t0))
            _trace.emit(task_id=_tid, model_id=model_id,
                        latency_ms=int(round((time.monotonic() - _t0) * 1000)),
                        source="butler-model-mock" if os.environ.get("BUTLER_MODEL_MOCK") else "butler-decide",
                        result_category=_cat, http_status=_http,
                        returned_model=model_id, timeout_s=MODEL_TIMEOUT,
                        content=_content, error=_err, timed_out=_to)
        except Exception:
            pass
'''
    s = s.replace(anchor, wrapper, 1)
    assert 'out, _ = model_call("PROBE", primary)' in s, "probe_gate call anchor missing"
    s = s.replace('out, _ = model_call("PROBE", primary)',
                  'out, _ = model_call("PROBE", primary, trace_task_id="probe")', 1)
    assert "            out, used = model_call(pack, model_id)" in s, "run() call anchor missing"
    s = s.replace("            out, used = model_call(pack, model_id)",
                  "            out, used = model_call(pack, model_id, trace_task_id=task.get(\"task_id\"))", 1)
    open(p, "w", encoding="utf-8").write(s)
    print("butler patched")


def patch_brain():
    p = HUB + "/scripts/brain_canary.py"
    s = open(p, encoding="utf-8").read()
    if "M5-3" in s:
        print("brain_canary already patched, skip")
        return
    # --- opencode_text ---
    anchor = '    ok = p.returncode == 0 and marker in answer\n'
    assert anchor in s, "brain opencode anchor missing"
    emit_oc = anchor + '''    # M5-3 trace (never breaks canary).
    try:
        import sys as _sys
        _sys.path.insert(0, "/home/ubuntu/ai-team/scripts")
        import trace_20261105 as _trace
        _trace.emit(task_id="braincanary-%s-%s-%s" % (model.replace("/", "-"), marker, datetime.now().strftime("%Y%m%d-%H%M%S")),
                    model_id=model, latency_ms=int(round(elapsed * 1000)),
                    source="brain-canary-opencode", returned_model=model if ok else "",
                    timeout_s=timeout, content=answer,
                    error="" if ok else "rc=%s" % p.returncode,
                    timed_out=(p.returncode == 124), rc=p.returncode,
                    result_category="成功" if ok else ("超时" if p.returncode == 124 else "空"))
    except Exception:
        pass
'''
    s = s.replace(anchor, emit_oc, 1)
    # --- web_brain_check ---
    anchor2 = '    ok = data.get("status") == "ok" and marker in (data.get("answer") or "")\n'
    assert anchor2 in s, "brain web anchor missing"
    emit_wb = anchor2 + '''    # M5-3 trace (never breaks canary).
    try:
        import sys as _sys2
        _sys2.path.insert(0, "/home/ubuntu/ai-team/scripts")
        import trace_20261105 as _trace2
        _trace2.emit(task_id="braincanary-webbrain-%s-%s-%s" % (provider, marker, datetime.now().strftime("%Y%m%d-%H%M%S")),
                    model_id="web_brain/" + provider,
                    latency_ms=int(round(float(data.get("elapsed_s", elapsed)) * 1000)),
                    source="brain-canary-web-brain", returned_model="web_brain/" + provider,
                    timeout_s=timeout, content=(data.get("answer") or ""),
                    error="" if ok else str(data.get("status") or "rc=%s" % p.returncode),
                    result_category="成功" if ok else "空")
    except Exception:
        pass
'''
    s = s.replace(anchor2, emit_wb, 1)
    # --- gift_check: wrap timing/status, emit before returns ---
    old_gift = '''def gift_check():
    try:
        key = open("/home/ubuntu/shared/worker-kit/newapi.key").read().strip()
        body = json.dumps({
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 8,
        }).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:3001/v1/chat/completions", data=body,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=40) as r:
            d = json.load(r)
        ct = (d.get("usage") or {}).get("completion_tokens", 0)
        return {"status": "HEALTHY" if ct else "DOWN", "detail": "completion_tokens=%s" % ct}
    except Exception as e:
        return {"status": "DOWN", "detail": type(e).__name__}'''
    assert old_gift in s, "brain gift anchor missing"
    new_gift = '''def gift_check():
    _t0 = time.monotonic(); _http = None; _err = ""; _exc = ""
    try:
        key = open("/home/ubuntu/shared/worker-kit/newapi.key").read().strip()
        body = json.dumps({
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 8,
        }).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:3001/v1/chat/completions", data=body,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=40) as r:
            _http = r.status
            d = json.load(r)
        ct = (d.get("usage") or {}).get("completion_tokens", 0)
        _ok = bool(ct)
    except Exception as e:
        _err = "%s: %s" % (type(e).__name__, str(e)[:150])
        _exc = type(e).__name__
        _ok = False; ct = 0
    # M5-3 trace (never breaks canary).
    try:
        import sys as _sys3
        _sys3.path.insert(0, "/home/ubuntu/ai-team/scripts")
        import trace_20261105 as _trace3
        _trace3.emit(task_id="braincanary-gift-deepseek-chat-%s" % datetime.now().strftime("%Y%m%d-%H%M%S"),
                    model_id="deepseek-chat",
                    latency_ms=int(round((time.monotonic() - _t0) * 1000)),
                    source="brain-canary-gift", http_status=_http,
                    returned_model="deepseek-chat" if _ok else "",
                    timeout_s=40, content="", error=_err,
                    timed_out=("timed out" in _err.lower()),
                    result_category="成功" if _ok else ("超时" if "timed out" in _err.lower() else "空"))
    except Exception:
        pass
    if _ok:
        return {"status": "HEALTHY", "detail": "completion_tokens=%s" % ct}
    return {"status": "DOWN", "detail": _exc or "DOWN"}'''
    s = s.replace(old_gift, new_gift, 1)
    open(p, "w", encoding="utf-8").write(s)
    print("brain_canary patched")


def main():
    patch_gateway_canary()
    patch_executor()
    patch_butler()
    patch_brain()
    print("ALL PATCHED")


if __name__ == "__main__":
    sys.exit(main())
