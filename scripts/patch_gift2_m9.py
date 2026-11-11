#!/usr/bin/env python3
"""patch_gift2_m9.py -- method 09: gift_check must not pass an explicit
category. With the HTTP code now captured (patch_gift_m9), the explicit
"空" contradicts verify's recompute (503) -- the line FAILS --verify.
classify() already derives the right category from http_status/timed_out/
content, so drop the explicit result_category and let it compute.
Backup ".bak-20261105-m9gift2" before writing.
Usage: python3 scripts/patch_gift2_m9.py [--check-only]
"""
import os
import sys

HUB = os.environ.get("M9_HUB", "/home/ubuntu/ai-team")
BAK_SUFFIX = ".bak-20261105-m9gift2"


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
        ('                    timeout_s=40, content=_txt if _ok else "", error=_err,\n                    timed_out=("timed out" in _err.lower()),\n                    result_category="成功" if _ok else ("超时" if "timed out" in _err.lower() else "空"))',
         '                    timeout_s=40, content=_txt if _ok else "", error=_err,\n                    timed_out=("timed out" in _err.lower()))  # M9-GIFT2: let classify() derive it from http_status'),
    ], marker="M9-GIFT2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
