#!/usr/bin/env python3
"""patch_trace_m9.py -- method 09 trace fixes (run on hub, 2026-11-05).

Part 1 (anchors verified 2026-11-05 21:0x CST from hub reads):
  A. brain_canary.py -- verify-consistency of the 3 M5-3 emit sites.
     The M5-3 patch passes result_category explicitly while also passing
     the full reply as content. scripts/trace-20261105.py --verify
     RECOMPUTES the category from the raw file (content + http + timed_out
     + rc). Where explicit != recomputed, verify FAILs:
     - opencode_text: rc==0 but marker missing -> explicit "空" vs
       recomputed "成功" (non-empty content). Fix: content="" when not ok
       (answer snippet moves into error, raw still keeps rc).
     - web_brain_check: same shape. Fix: content="" when not ok.
     - gift_check: passes content="" ALWAYS, so a successful call
       (explicit "成功") recomputes as "空" -> verify FAIL on every
       success. Fix: capture the reply text and pass it when ok.
  B. c2-executor-20261104.py patrol bounded call:
     - HTTPError path never recorded the status code (_pllm_http stayed
       None), so a 429/503 would classify as "空" instead of "429"/"503".
       Fix: capture getattr(e, "code", None) in the except branch.
     - returned_model was hardcoded "" even on success. Fix: capture
       _d.get("model") and pass it.

Safety: every patch is an exact-anchor replace; any anchor mismatch aborts
BEFORE any write. Backups ``.bak-20261105-m9trace`` are made before writing.
All emits stay inside the existing guarded try/except (trace can never break
the caller). No caller behaviour changes except the recorded fields.
Usage: python3 scripts/patch_trace_m9.py [--check-only]
"""
import sys

HUB = "/home/ubuntu/ai-team"
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


def main():
    # ---- A. brain_canary.py ----
    patch(HUB + "/scripts/brain_canary.py", [
        ("""                    timeout_s=timeout, content=answer,
                    error="" if ok else "rc=%s" % p.returncode,""",
         """                    # M9-TRACE-VERIFY: content empty on failure so --verify recompute agrees.
                    timeout_s=timeout, content=answer if ok else "",
                    error="" if ok else "rc=%s answer=%s" % (p.returncode, answer[:120]),"""),
        ("""                    timeout_s=timeout, content=(data.get("answer") or ""),
                    error="" if ok else str(data.get("status") or "rc=%s" % p.returncode),""",
         """                    timeout_s=timeout, content=(data.get("answer") or "") if ok else "",
                    error="" if ok else str(data.get("status") or "rc=%s" % p.returncode) + " answer=" + (data.get("answer") or "")[:120],"""),
        ("""    _t0 = time.monotonic(); _http = None; _err = ""; _exc = ""
""",
         """    _t0 = time.monotonic(); _http = None; _err = ""; _exc = ""; _txt = ""
"""),
        ("""        ct = (d.get("usage") or {}).get("completion_tokens", 0)
        _ok = bool(ct)""",
         """        ct = (d.get("usage") or {}).get("completion_tokens", 0)
        _txt = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        _ok = bool(ct)"""),
        ("""                    timeout_s=40, content="", error=_err,""",
         """                    timeout_s=40, content=_txt if _ok else "", error=_err,"""),
    ], marker="M9-TRACE-VERIFY")

    # ---- B. c2-executor-20261104.py patrol bounded call ----
    patch(HUB + "/scripts/c2-executor-20261104.py", [
        ("""                _pllm_t0 = _time.monotonic(); _pllm_http = None; _pllm_txt = ""; _pllm_err = ""; _pllm_to = False""",
         """                # M9-TRACE-VERIFY: capture http code + returned model for the trace line.
                _pllm_t0 = _time.monotonic(); _pllm_http = None; _pllm_txt = ""; _pllm_err = ""; _pllm_to = False; _pllm_rm = """""),
        ("""                    _txt = ((_d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                    _pllm_txt = _txt""",
         """                    _txt = ((_d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                    _pllm_txt = _txt
                    _pllm_rm = _d.get("model", "") or """""),
        ("""                except Exception as _pllm_e:
                    _pllm_err = str(_pllm_e)[:200]
                    _pllm_to = "timed out" in str(_pllm_e).lower()""",
         """                except Exception as _pllm_e:
                    _pllm_err = str(_pllm_e)[:200]
                    _pllm_http = getattr(_pllm_e, "code", None)
                    _pllm_to = "timed out" in str(_pllm_e).lower()"""),
        ("""                                 source="c2-executor-patrol", http_status=_pllm_http,
                                 returned_model="", timeout_s=60, content=_pllm_txt,""",
         """                                 source="c2-executor-patrol", http_status=_pllm_http,
                                 returned_model=_pllm_rm, timeout_s=60, content=_pllm_txt,"""),
    ], marker="M9-TRACE-VERIFY")
    return 0


if __name__ == "__main__":
    sys.exit(main())
