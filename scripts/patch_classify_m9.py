#!/usr/bin/env python3
"""patch_classify_m9.py -- method 09: fence-aware JSON classification.

M9 finding: gateway_canary t2 returns fence-wrapped JSON
("```json\\n{...}\\n```"). The canary itself strips fences and scores
t2=True, but trace classify() only did .strip("`"), leaving the "json"
tag, so json.loads failed and the call was recorded as "格式错" -- a
false positive (live proof: gwcanary-m9-20261105-glm-4-flash-cc-t2,
raw file shows valid fenced JSON, canary t2_json=true).
Fix: strip ``` fences (same rule as gateway_canary) before json.loads.
Only genuinely unparsable text stays "格式错". Plus a regression test
in self_test (classify_json_fenced).
Backup ".bak-20261105-m9trace" before writing. Hyphen file only; the
underscore shim re-exports from it, no shim change needed.
Usage: python3 scripts/patch_classify_m9.py [--check-only]
Env override for dry-run testing: M9_HUB=/tmp/m9test3
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


OLD = '    if expect_json:\n        try:\n            json.loads((content or "").strip().strip("`"))\n        except Exception:\n            return "格式错"'

NEW = '    if expect_json:\n        # Fence-wrapped JSON is valid JSON (canary strips ``` fences the\n        # same way); only genuinely unparsable text is 格式错. (M9 fix:\n        # a fence-wrapped t2 reply was misclassified as 格式错.)\n        s = (content or "").strip()\n        if s.startswith("```"):\n            ls = s.splitlines()\n            s = "\\n".join(ls[1:-1]) if len(ls) > 2 else s\n        try:\n            json.loads(s.strip())\n        except Exception:\n            return "格式错"'

OLD_TEST = "    tests.append((\"classify_json_bad\", classify(http_status=200, content='not json', expect_json=True) == \"格式错\"))"

NEW_TEST = "    tests.append((\"classify_json_bad\", classify(http_status=200, content='not json', expect_json=True) == \"格式错\"))\n    tests.append((\"classify_json_fenced\", classify(http_status=200, content='```json\\n{\"a\":1}\\n```', expect_json=True) == \"成功\"))  # M9-CLASSIFY-FENCE"


def main():
    patch(HUB + "/scripts/trace-20261105.py", [
        (OLD, NEW),
        (OLD_TEST, NEW_TEST),
    ], marker="M9-CLASSIFY-FENCE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
