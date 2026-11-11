#!/usr/bin/env python3
"""M4 失败分类写入器 (2026-11-07 M3/M4/M5 落地扫描补齐).

缺口: 9 类失败分类 (F-CONTRACT/F-VERIFY/F-ROUTING/F-429/F-503/F-TIMEOUT/
F-EMPTY/F-FORMAT/F-UNDIAG) 只存在于 MECHANISM-20261106.md 文档与
weekly-audit/retrywait-scan 的读侧 (读 payload.failure_class), 从没有任何
代码在失败发生时写出 failure_class. 本脚本是唯一的分类写入点.

判定顺序固定 (MECHANISM-20261106.md 第 1 节所列顺序):
  F-CONTRACT > F-VERIFY > F-ROUTING > F-429 > F-503 > F-TIMEOUT >
  F-EMPTY > F-FORMAT > F-UNDIAG

用法:
  python3 m4-failure-classify.py --text "gate refused: missing verifier" [--http-status 429]
  python3 m4-failure-classify.py --self-test   # 9 类正例 + 顺序优先反例, 零外部依赖
只读/纯函数, 不写库、不花钱、不对外发. Stdlib only.
"""
import argparse
import json
import sys

CLASSES = ("F-CONTRACT", "F-VERIFY", "F-ROUTING", "F-429", "F-503",
           "F-TIMEOUT", "F-EMPTY", "F-FORMAT", "F-UNDIAG")

# (class, [substring signals, lowercased]) — checked in CLASSES order.
_RULES = [
    ("F-CONTRACT", ["taskcontract", "gate refused", "missing verifier",
                    "missing evidence", "missing field", "no evidence",
                    "no verifier", "no output_path", "missing output_path",
                    "contract invalid", "forbidden", "no contract"]),
    ("F-VERIFY", ["verifier", "verification failed", "review-hook",
                  "review hook", "verdict fail", "evidence mismatch",
                  "sha mismatch", "artifact mismatch", "min_bytes"]),
    ("F-ROUTING", ["unsupported_type", "wrong worker", "wrong seat",
                   "misrout", "routing", "routed to wrong",
                   "not my task", "seat wrote code", "wrote code for worker"]),
    ("F-429", ["429", "rate limit", "rate_limit", "too many requests"]),
    ("F-503", ["503", "service unavailable", "overloaded", "bad gateway",
               "502"]),
    ("F-TIMEOUT", ["timeout", "timed out", "deadline exceeded",
                   "heartbeat lost", "no heartbeat"]),
    ("F-EMPTY", ["empty response", "empty output", "empty result",
                 "no content", "returned nothing", "blank response",
                 "empty reason"]),
    ("F-FORMAT", ["json parse", "not json", "invalid json", "format error",
                  "unexpected token", "unparseable", "bad format",
                  "not a json"]),
]


def classify(text="", http_status=None):
    """Return one of the 9 classes. http_status 429/503 short-circuits its class
    only after the three structural classes (CONTRACT/VERIFY/ROUTING)."""
    t = (text or "").lower()
    for cls, signals in _RULES:
        if cls in ("F-429", "F-503"):
            if http_status is not None and str(http_status) == cls[2:]:
                return cls
        if any(s in t for s in signals):
            return cls
    if http_status is not None and str(http_status) in ("429", "503"):
        return "F-" + str(http_status)
    return "F-UNDIAG"


def self_test():
    cases = [
        # (text, http_status, expected) — one positive per class.
        ("gate refused: missing verifier field", None, "F-CONTRACT"),
        ("verifier failed: evidence mismatch", None, "F-VERIFY"),
        ("worker got unsupported_type_patrol", None, "F-ROUTING"),
        ("http 429 too many requests", 429, "F-429"),
        ("service unavailable 503", 503, "F-503"),
        ("call timed out after 120s", None, "F-TIMEOUT"),
        ("empty response from model", None, "F-EMPTY"),
        ("json parse error: unexpected token", None, "F-FORMAT"),
        ("something odd happened, no clue", None, "F-UNDIAG"),
        # Precedence negatives: earlier class in the fixed order wins.
        ("gate refused but also 429 rate limit", 429, "F-CONTRACT"),
        ("verifier failed and response timed out", None, "F-VERIFY"),
        ("wrong worker routing, 503 overloaded", 503, "F-ROUTING"),
        ("rate limit 429 and timed out", None, "F-429"),
        ("503 overloaded then timed out", None, "F-503"),
        ("timed out with empty response", None, "F-TIMEOUT"),
        ("empty response, invalid json", None, "F-EMPTY"),
        ("", 429, "F-429"),
        ("", 503, "F-503"),
        ("", 500, "F-UNDIAG"),
        ("", None, "F-UNDIAG"),
    ]
    bad = 0
    for text, st, want in cases:
        got = classify(text, st)
        ok = got == want
        print("%s classify(%r, %r) = %s (want %s)" % ("PASS" if ok else "FAIL", text, st, got, want))
        bad += 0 if ok else 1
    print("SELF-TEST: %d/%d passed" % (len(cases) - bad, len(cases)))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="M4 9-class failure classifier (write side)")
    ap.add_argument("--text", default="")
    ap.add_argument("--http-status", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    st = None
    if a.http_status is not None:
        try:
            st = int(a.http_status)
        except ValueError:
            st = a.http_status
    cls = classify(a.text, st)
    if a.json:
        print(json.dumps({"failure_class": cls}, ensure_ascii=False))
    else:
        print(cls)
    return 0


if __name__ == "__main__":
    sys.exit(main())
