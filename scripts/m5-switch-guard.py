#!/usr/bin/env python3
"""M5 人兜底写拦截 (2026-11-07 M3/M4/M5 落地扫描补齐).

缺口: state/m5-20261105/check-m5-switch.sh 只是只读检查 (开关/信封当前值),
没有任何代码拦截"把 envelope_status / autonomous_* 改为 OPEN"的写操作;
信封四项 (单笔上限/日累计上限/退款规则/模板合同) 齐全性也只在文档里,
check 脚本只验了 template_contract_status 一项.
本脚本是写前拦截器: 任何要落盘的新版 switch/envelope JSON, 必须先过此门.

拦截规则 (任一违反即 DENY, exit 1; 与 M5 定案逐条对应):
  1. autonomous_payout 永远 FORBIDDEN_ALWAYS, 任何改动即拒.
  2. autonomous_order_taking / autonomous_receipt / envelope_status 由
     CLOSED 改为非 CLOSED (开启): 只许董事长; 且信封四项须齐全
     (per_order_cap_cny>0, daily_cumulative_cap_cny>0,
     refund_rules 非空, template_contract 非空且 status==PROVIDED).
  3. 开 autonomous_receipt 另须首 3 单已确认 3/3 (confirmed_count>=3).
  4. 16 类情形: id 与顺序须与旧版一致, 每类 escalate_to 须为董事长.
  5. budget_line.autonomous_spend_cny 改动只许董事长 (且仍须为 0 才能开收款).
  6. first_three_orders.confirmed_count 每次最多 +1 且只许董事长改;
     required_confirmations 须恒为 3.
  7. 信封开启时 opened_by 须为董事长.

用法:
  python3 m5-switch-guard.py --old-switch S --old-env E --new-switch S2 --new-env E2 \
      --actor 董事长
  python3 m5-switch-guard.py --self-test   # 1 正例 + 7 反例, 零外部依赖
只读 (不写 switch/envelope). Stdlib only.
"""
import argparse
import copy
import json
import sys

CHAIRMAN = "董事长"
CATEGORIES = ["PAYMENT_IN", "PAYMENT_OUT", "CONTRACT", "QUOTE_OVER_TEMPLATE", "DISCOUNT",
              "OUTREACH_NEW", "COMPLAINT", "DISPUTE", "LEGAL_TAX", "PLATFORM_RISK",
              "PII_PRIVACY", "GUARDRAIL_HIT", "SECURITY_EVENT", "BUDGET_BREACH",
              "ERROR_RATE_BREACH", "FIRST_THREE_ORDERS"]
OPEN_FIELDS = ("autonomous_order_taking", "autonomous_receipt", "envelope_status")


def envelope_complete(env):
    """信封四项齐全: 单笔上限/日累计上限/退款规则/模板合同."""
    problems = []
    for k in ("per_order_cap_cny", "daily_cumulative_cap_cny"):
        v = env.get(k)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
            problems.append("信封缺 %s (须为 >0 的数)" % k)
    if not env.get("refund_rules"):
        problems.append("信封缺 refund_rules (退款规则)")
    if not env.get("template_contract"):
        problems.append("信封缺 template_contract (模板合同文本)")
    if env.get("template_contract_status") != "PROVIDED":
        problems.append("模板合同状态非 PROVIDED (现为 %s)" % env.get("template_contract_status"))
    return problems


def validate_transition(old_sw, old_env, new_sw, new_env, actor):
    """Return list of deny reasons (empty = ALLOW)."""
    deny = []
    if new_sw.get("autonomous_payout") != "FORBIDDEN_ALWAYS":
        deny.append("自主支出须永远 FORBIDDEN_ALWAYS (现为 %s)" % new_sw.get("autonomous_payout"))
    opening = [f for f in OPEN_FIELDS
               if old_sw.get(f) == "CLOSED" and new_sw.get(f) not in ("CLOSED", None)]
    if opening:
        if actor != CHAIRMAN:
            deny.append("开启 %s 只许董事长 (申请人 %s)" % (opening, actor))
        for p in envelope_complete(new_env):
            deny.append("开启 %s 但%s" % (opening, p))
        if "autonomous_receipt" in opening:
            got = (new_sw.get("first_three_orders") or {}).get("confirmed_count", 0)
            if got < 3:
                deny.append("开自主收款须首 3 单已确认 3/3 (现为 %s/3)" % got)
        if new_env.get("status") not in ("CLOSED", None) and new_env.get("opened_by") != CHAIRMAN:
            deny.append("信封开启人须为董事长 (现为 %s)" % new_env.get("opened_by"))
    new_ids = [c.get("id") for c in new_sw.get("categories", [])]
    if new_ids != CATEGORIES:
        deny.append("16 类情形 id/顺序被改 (须与定案一致)")
    elif not all(c.get("escalate_to") == CHAIRMAN for c in new_sw.get("categories", [])):
        deny.append("有类别 escalate_to 不是董事长")
    old_spend = (old_sw.get("budget_line") or {}).get("autonomous_spend_cny")
    new_spend = (new_sw.get("budget_line") or {}).get("autonomous_spend_cny")
    if new_spend != old_spend and actor != CHAIRMAN:
        deny.append("改自主支出上限只许董事长")
    if new_spend not in (0, None) and "autonomous_receipt" in opening:
        deny.append("开自主收款时自主支出上限须为 0")
    old_ft = old_sw.get("first_three_orders") or {}
    new_ft = new_sw.get("first_three_orders") or {}
    if new_ft.get("required_confirmations") != 3:
        deny.append("首单确认数须恒为 3")
    oc, nc = old_ft.get("confirmed_count", 0), new_ft.get("confirmed_count", 0)
    if nc != oc:
        if actor != CHAIRMAN:
            deny.append("改首单已确认数只许董事长")
        elif nc != oc + 1:
            deny.append("首单已确认数每次最多 +1 (现 %s -> %s)" % (oc, nc))
    return deny


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def self_test():
    base_sw = {"status": "ACTIVE", "autonomous_order_taking": "CLOSED",
               "autonomous_receipt": "CLOSED", "autonomous_payout": "FORBIDDEN_ALWAYS",
               "envelope_status": "CLOSED",
               "categories": [{"id": c, "escalate_to": "董事长"} for c in CATEGORIES],
               "budget_line": {"autonomous_spend_cny": 0},
               "first_three_orders": {"required_confirmations": 3, "confirmed_count": 0}}
    base_env = {"status": "CLOSED", "opened_by": None, "per_order_cap_cny": None,
                "daily_cumulative_cap_cny": None, "refund_rules": None,
                "template_contract": None, "template_contract_status": "NOT_PROVIDED"}
    full_env = dict(base_env, status="OPEN", opened_by="董事长", per_order_cap_cny=500,
                    daily_cumulative_cap_cny=2000, refund_rules="7 天无理由",
                    template_contract="合同文本", template_contract_status="PROVIDED")

    def mod(sw=None, env=None):
        return (copy.deepcopy(base_sw if sw is None else sw),
                copy.deepcopy(base_env if env is None else env))

    cases = []  # (old_sw, old_env, new_sw, new_env, actor, want_allow, msg)
    # 正例: 旧态首单已是 3/3 (此前逐单一确认), 董事长 + 信封四项齐全 -> 开收款 ALLOW.
    old_s, _ = mod()
    old_s["first_three_orders"]["confirmed_count"] = 3
    s, e = mod()
    s["autonomous_receipt"] = "OPEN"; s["envelope_status"] = "OPEN"
    s["first_three_orders"]["confirmed_count"] = 3
    cases.append((old_s, dict(base_env), s, dict(full_env), "董事长", True,
                  "董事长+四项齐全+3/3确认开收款通过"))
    # 反例 1: 非董事长开启.
    s, e = mod()
    s["autonomous_order_taking"] = "OPEN"
    cases.append((base_sw, base_env, s, dict(full_env), "twt", False, "非董事长开启被拒"))
    # 反例 2: 董事长开但信封四项缺 (沿用空信封).
    s, e = mod()
    s["autonomous_order_taking"] = "OPEN"
    cases.append((base_sw, base_env, s, dict(base_env), "董事长", False, "信封四项不全开启被拒"))
    # 反例 3: 首 3 单未满 3/3 就开收款.
    s, e = mod()
    s["autonomous_receipt"] = "OPEN"
    s["first_three_orders"]["confirmed_count"] = 2
    cases.append((base_sw, base_env, s, dict(full_env), "董事长", False, "首单 2/3 开收款被拒"))
    # 反例 4: 改自主支出.
    s, e = mod()
    s["autonomous_payout"] = "OPEN"
    cases.append((base_sw, base_env, s, dict(base_env), "董事长", False, "自主支出改 OPEN 被拒"))
    # 反例 5: 类别 escalate_to 被改.
    s, e = mod()
    s["categories"][0]["escalate_to"] = "twt"
    cases.append((base_sw, base_env, s, dict(base_env), "董事长", False, "类别上报对象被改被拒"))
    # 反例 6: 首单计数一次 +2.
    s, e = mod()
    s["first_three_orders"]["confirmed_count"] = 2
    cases.append((base_sw, base_env, s, dict(base_env), "董事长", False, "首单计数一次+2 被拒"))
    # 反例 7: 无改动 (现状复核) 通过.
    s, e = mod()
    cases.append((base_sw, base_env, s, dict(base_env), "twt", True, "无改动现状通过"))
    bad = 0
    for old_sw, old_env, new_sw, new_env, actor, want_allow, msg in cases:
        reasons = validate_transition(old_sw, old_env, new_sw, new_env, actor)
        ok = (not reasons) == want_allow
        print("%s %s %s" % ("PASS" if ok else "FAIL", msg,
                            ("(%s)" % "; ".join(reasons)) if reasons else ""))
        bad += 0 if ok else 1
    print("SELF-TEST: %d/%d passed" % (len(cases) - bad, len(cases)))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="M5 human-fallback write guard (intercept before save)")
    ap.add_argument("--old-switch", default=None)
    ap.add_argument("--old-env", default=None)
    ap.add_argument("--new-switch", default=None)
    ap.add_argument("--new-env", default=None)
    ap.add_argument("--actor", default=None)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not (a.old_switch and a.old_env and a.new_switch and a.new_env and a.actor):
        ap.print_help()
        return 2
    reasons = validate_transition(_load(a.old_switch), _load(a.old_env),
                                  _load(a.new_switch), _load(a.new_env), a.actor)
    if reasons:
        print("DENY: 此次改动被 M5 人兜底拦截")
        for r in reasons:
            print("  - " + r)
        return 1
    print("ALLOW: 改动符合 M5 人兜底规则, 可以落盘")
    return 0


if __name__ == "__main__":
    sys.exit(main())
