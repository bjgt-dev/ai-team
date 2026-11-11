#!/usr/bin/env python3
"""M4 pause/freeze 硬护栏校验器 (2026-11-07 M3/M4/M5 落地扫描补齐, 令19).

缺口: 令19 五硬护栏之一要求 pause/freeze 须带 owner + reason + expires_at,
但枢纽只有 check_freeze.py (代码冻结 sha 校验, 另一回事), 无任何代码校验
pause/freeze 记录本身. 本脚本是该护栏的校验点: 任何 pause/freeze 记录
须过此门才能生效.

规则 (缺一不可, 否则 exit 1):
  owner: 非空字符串 (谁挂起的, 须可追责)
  reason: 非空字符串 (为什么挂起)
  expires_at: ISO8601 时间, 且在未来 (挂起必须有到期, 不许无限期冻结)

用法:
  python3 m4-pause-freeze-gate.py --record pause.json
  python3 m4-pause-freeze-gate.py --template   # 打印合规模板
  python3 m4-pause-freeze-gate.py --self-test  # 缺 owner/缺 reason/过期/坏日期反例
只读 (除 --template 打印外不写任何文件). Stdlib only.
"""
import argparse
import json
import sys
from datetime import datetime, timezone

TEMPLATE = {
    "action": "pause",
    "target": "任务/席位/定时器 id",
    "owner": "twt",
    "reason": "挂起原因 (一句话)",
    "expires_at": "2026-11-08T12:00:00+08:00",
}


def _parse_time(s):
    if not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def validate(rec):
    """Return list of problems (empty = pass)."""
    problems = []
    if not isinstance(rec, dict):
        return ["记录不是 JSON 对象"]
    if not isinstance(rec.get("owner"), str) or not rec.get("owner").strip():
        problems.append("缺 owner (须为非空字符串)")
    if not isinstance(rec.get("reason"), str) or not rec.get("reason").strip():
        problems.append("缺 reason (须为非空字符串)")
    exp = _parse_time(rec.get("expires_at"))
    if exp is None:
        problems.append("缺 expires_at 或格式非法 (须 ISO8601)")
    elif exp <= datetime.now(timezone.utc):
        problems.append("expires_at 已过期 (%s), 不许无限期/过期冻结" % rec.get("expires_at"))
    return problems


def self_test():
    from datetime import timedelta
    future = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    cases = [
        ({"action": "pause", "owner": "twt", "reason": "等董事长定",
          "expires_at": future}, True, "完整记录通过"),
        ({"action": "pause", "reason": "等董事长定", "expires_at": future},
         False, "缺 owner 被拒"),
        ({"action": "freeze", "owner": "twt", "expires_at": future},
         False, "缺 reason 被拒"),
        ({"action": "pause", "owner": "twt", "reason": "x", "expires_at": past},
         False, "过期 expires_at 被拒"),
        ({"action": "pause", "owner": "twt", "reason": "x", "expires_at": "明天"},
         False, "非法日期被拒"),
        ({"action": "pause", "owner": "  ", "reason": "x", "expires_at": future},
         False, "空白 owner 被拒"),
        ("not-a-dict", False, "非对象记录被拒"),
    ]
    bad = 0
    for rec, want_pass, msg in cases:
        probs = validate(rec)
        ok = (not probs) == want_pass
        print("%s %s %s" % ("PASS" if ok else "FAIL", msg,
                            ("(%s)" % "; ".join(probs)) if probs else ""))
        bad += 0 if ok else 1
    print("SELF-TEST: %d/%d passed" % (len(cases) - bad, len(cases)))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="M4 pause/freeze gate: owner+reason+expires_at")
    ap.add_argument("--record", default=None)
    ap.add_argument("--template", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if a.template:
        print(json.dumps(TEMPLATE, ensure_ascii=False, indent=2))
        return 0
    if a.record:
        try:
            rec = json.load(open(a.record, encoding="utf-8"))
        except Exception as exc:
            print("FAIL: 读不到记录 %s (%s)" % (a.record, exc))
            return 1
        probs = validate(rec)
        if probs:
            for p in probs:
                print("FAIL: " + p)
            return 1
        print("PASS: pause/freeze 记录合规 (owner/reason/未过期 expires_at 齐全)")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
