#!/usr/bin/env python3
"""M3 合同门禁生产审计 (2026-11-07 M3/M4/M5 落地扫描补齐).

缺口: dispatch_task.py 的门禁只覆盖走该脚本的派单; 其 docstring 自认
"ad-hoc psql inserts bypass the gate by construction", timer/patrol 提交器
虽已补固定合同 (M3 残留卡-1), 但没有任何代码定期审计库里是否已有绕过门禁
的合同行. 本脚本是只读审计: 扫 central_tasks, 找出带合同却无门禁戳的行.

判定 (纯函数 classify_payload, 可单测):
  gated            payload 有 contract 且 m3_gate ∈ {enforced-20261105, disabled-bypass}
  bypassed         m3_gate == disabled-bypass (已记录的回滚模式, 单独计数, 非违规但告警)
  ungated-contract payload 有 contract 但无 m3_gate (违规: 绕过门禁入库)
  system           无 contract (timer/patrol 系统任务, 不违规)

用法:
  python3 m3-gate-audit.py [--days 30] [--limit 20]   # 只读 SELECT, 不写库
  python3 m3-gate-audit.py --self-test                # 纯函数正反例, 零 DB
有 ungated-contract 行则 exit 1. DSN 口径与 dispatch_task.py 相同.
"""
import argparse
import sys

GATED = ("enforced-20261105", "disabled-bypass")
# 门禁上线时刻 (M3 五项 2026-11-05 20:28 测试通过; 此前入库的行无戳属历史遗留, 不判违规).
GATE_LIVE = "2026-11-05T20:30:00+00:00"


def classify_payload(payload, created_at=None):
    if not isinstance(payload, dict):
        return "system"
    if payload.get("contract") is None:
        return "system"
    gate = payload.get("m3_gate")
    if gate in GATED:
        return "bypassed" if gate == "disabled-bypass" else "gated"
    if created_at is not None and str(created_at) < GATE_LIVE:
        return "pre-gate"
    return "ungated-contract"


def live_audit(days, limit):
    import psycopg
    dsn = ""
    for line in open("/home/ubuntu/ai-team/state/.cq-dsn", encoding="utf-8"):
        line = line.strip()
        if line.startswith("CQ_DSN="):
            dsn = line[len("CQ_DSN="):].strip().strip('"').strip("'")
    if not dsn:
        print("FAIL: 无 DSN, fail closed")
        return 2
    sql = ("SELECT payload->>'task_id', status, payload, created_at FROM central_tasks "
           "WHERE created_at >= now() - (%s || ' days')::interval "
           "AND payload ? 'contract' ORDER BY created_at DESC")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (str(days),))
            rows = cur.fetchall()
    counts = {"gated": 0, "bypassed": 0, "ungated-contract": 0, "pre-gate": 0}
    bad = []
    for tid, status, payload, created_at in rows:
        c = classify_payload(payload, created_at.isoformat() if created_at else None)
        counts[c] = counts.get(c, 0) + 1
        if c == "ungated-contract":
            bad.append((tid, status))
    print("审计窗口: 近 %d 天, 带合同行 %d: 门禁通过 %d / kill-switch 绕过 %d / 门禁上线前遗留 %d / 无门禁戳 %d"
          % (days, len(rows), counts["gated"], counts["bypassed"], counts["pre-gate"],
             counts["ungated-contract"]))
    for tid, status in bad[:limit]:
        print("UNGATED: task_id=%s status=%s" % (tid, status))
    if bad:
        print("RESULT: FAIL (%d 行绕过门禁入库, 须人工核查来源)" % len(bad))
        return 1
    if counts["bypassed"]:
        print("RESULT: PASS (附告警: %d 行 kill-switch 绕过, 请确认回滚模式已关闭)"
              % counts["bypassed"])
    else:
        print("RESULT: PASS")
    return 0


def self_test():
    cases = [
        ({"contract": {"task_id": "x"}, "m3_gate": "enforced-20261105"}, None, "gated", "门禁戳通过"),
        ({"contract": {"task_id": "x"}, "m3_gate": "disabled-bypass"}, None, "bypassed", "绕过模式单独计数"),
        ({"contract": {"task_id": "x"}}, "2026-11-07T00:00:00+00:00", "ungated-contract", "门禁上线后有合同无门禁戳判违规"),
        ({"contract": {"task_id": "x"}}, "2026-11-05T13:35:00+00:00", "pre-gate", "门禁上线前遗留行不判违规"),
        ({"contract": {"task_id": "x"}, "m3_gate": "other"}, "2026-11-07T00:00:00+00:00", "ungated-contract", "未知门禁戳判违规"),
        ({"type": "timer"}, None, "system", "无合同系统任务不违规"),
        ({}, None, "system", "空 payload 不违规"),
        (None, None, "system", "非对象不违规"),
    ]
    bad = 0
    for payload, created_at, want, msg in cases:
        got = classify_payload(payload, created_at)
        ok = got == want
        print("%s %s: %s (want %s)" % ("PASS" if ok else "FAIL", msg, got, want))
        bad += 0 if ok else 1
    print("SELF-TEST: %d/%d passed" % (len(cases) - bad, len(cases)))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="M3 contract-gate production audit (read-only)")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    return live_audit(a.days, a.limit)


if __name__ == "__main__":
    sys.exit(main())
