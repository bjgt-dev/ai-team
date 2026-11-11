#!/usr/bin/env python3
"""patch_c2_m9.py -- method 09: c2-executor patrol trace fixes (run on hub).

  - HTTPError path never recorded the status code (_pllm_http stayed None),
    so a 429/503 would classify as "空" instead of "429"/"503".
    Fix: capture getattr(e, "code", None) in the except branch.
  - returned_model was hardcoded "" even on success.
    Fix: capture _d.get("model") and pass it.

Safety: exact-anchor replaces; any anchor mismatch aborts BEFORE any
write. Backup ".bak-20261105-m9trace" is made before writing. The emit
stays inside the existing guarded try/except (trace can never break the
caller). No caller behaviour changes except the recorded fields.
Usage: python3 scripts/patch_c2_m9.py [--check-only]
Env override for dry-run testing: M9_HUB=/tmp/m9test
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


def main():
    patch(HUB + "/scripts/c2-executor-20261104.py", [
        ('                _pllm_t0 = _time.monotonic(); _pllm_http = None; _pllm_txt = ""; _pllm_err = ""; _pllm_to = False',
         '                _pllm_t0 = _time.monotonic(); _pllm_http = None; _pllm_txt = ""; _pllm_err = ""; _pllm_to = False; _pllm_rm = ""  # M9-TRACE-VERIFY'),
        ('                    _txt = ((_d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""\n                    _pllm_txt = _txt',
         '                    _txt = ((_d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""\n                    _pllm_txt = _txt\n                    _pllm_rm = _d.get("model", "") or ""'),
        ('                except Exception as _pllm_e:\n                    _pllm_err = str(_pllm_e)[:200]\n                    _pllm_to = "timed out" in str(_pllm_e).lower()',
         '                except Exception as _pllm_e:\n                    _pllm_err = str(_pllm_e)[:200]\n                    _pllm_http = getattr(_pllm_e, "code", None)\n                    _pllm_to = "timed out" in str(_pllm_e).lower()'),
        ('                                 source="c2-executor-patrol", http_status=_pllm_http,\n                                 returned_model="", timeout_s=60, content=_pllm_txt,',
         '                                 source="c2-executor-patrol", http_status=_pllm_http,\n                                 returned_model=_pllm_rm, timeout_s=60, content=_pllm_txt,'),
    ], marker="M9-TRACE-VERIFY")
    return 0


if __name__ == "__main__":
    sys.exit(main())
