#!/usr/bin/env python3
"""patch_gift_m9.py -- method 09: gift_check must capture the HTTP status.

M9 finding (live proof 2026-11-05 21:52): gift_check hit HTTP 503 in 4 ms
but recorded http_status=None, so the trace line classified as "空"
instead of "503" -- the error text says 503, the code was just never
captured (same flaw as the c2 patrol call, fixed in part 1).
Fix: _http = getattr(e, "code", None) in the except branch.
The already-written line is left as-is (verify recomputes from its raw
file, which has no code; editing it would break --verify). Re-running
gift_check after this fix produces a correctly classified line.
Backup ".bak-20261105-m9gift" before writing.
Usage: python3 scripts/patch_gift_m9.py [--check-only]
"""
import os
import sys

HUB = os.environ.get("M9_HUB", "/home/ubuntu/ai-team")
BAK_SUFFIX = ".bak-20261105-m9gift"


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
    patch(HUB + "/scripts/brain_canary.py", [
        ('    except Exception as e:\n        _err = "%s: %s" % (type(e).__name__, str(e)[:150])\n        _exc = type(e).__name__\n        _ok = False; ct = 0',
         '    except Exception as e:  # M9-GIFT-HTTP: capture the status code for the trace line.\n        _http = getattr(e, "code", None)\n        _err = "%s: %s" % (type(e).__name__, str(e)[:150])\n        _exc = type(e).__name__\n        _ok = False; ct = 0'),
    ], marker="M9-GIFT-HTTP")
    return 0


if __name__ == "__main__":
    sys.exit(main())
