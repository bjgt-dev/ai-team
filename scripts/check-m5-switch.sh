#!/usr/bin/env bash
# check-m5-switch.sh — M5 人兜底硬开关校验（只读检查，不改状态，不对外发）
# 规范版位置：枢纽 ~/ai-team/scripts/check-m5-switch.sh
# 原版在 state/m5-20261105/check-m5-switch.sh（2026-11-05）；本版与原版同逻辑，
# 仅把状态目录改为环境变量 M5_STATE_DIR（默认枢纽 state/m5-20261105），
# 使正反测试可在 /tmp 副本上跑而不碰生产文件。
# 用法：bash check-m5-switch.sh ；退出 0 = 全部通过，1 = 有 FAIL
set -u
STATE_DIR="${M5_STATE_DIR:-/home/ubuntu/ai-team/state/m5-20261105}"
SWITCH="$STATE_DIR/human-fallback-switch.json"
ENV="$STATE_DIR/envelope.json"
[ -f "$SWITCH" ] || { echo "FAIL: 开关文件缺失 $SWITCH"; echo "RESULT: FAIL"; exit 1; }
[ -f "$ENV" ] || { echo "FAIL: 信封文件缺失 $ENV"; echo "RESULT: FAIL"; exit 1; }
echo "PASS: 开关文件存在 $SWITCH"
echo "PASS: 信封文件存在 $ENV"
python3 - "$SWITCH" "$ENV" <<'PYEOF'
import json, sys
sw = json.load(open(sys.argv[1]))
env = json.load(open(sys.argv[2]))
fails = []
def chk(cond, msg):
    print(("PASS: " if cond else "FAIL: ") + msg)
    if not cond:
        fails.append(msg)
chk(sw.get("status") == "ACTIVE", "开关状态 ACTIVE")
chk(sw.get("autonomous_order_taking") == "CLOSED", "自主接单默认关闭 CLOSED")
chk(sw.get("autonomous_receipt") == "CLOSED", "自主收款默认关闭 CLOSED")
chk(sw.get("autonomous_payout") == "FORBIDDEN_ALWAYS", "自主支出永远禁止 FORBIDDEN_ALWAYS")
chk(sw.get("envelope_status") == "CLOSED", "信封关闭 CLOSED")
chk(env.get("status") == "CLOSED", "信封模板关闭 CLOSED")
chk(env.get("opened_by") is None, "信封未被任何人开启（opened_by 为空）")
chk(env.get("template_contract_status") == "NOT_PROVIDED", "模板合同未提供，信封不可开启")
expected = ["PAYMENT_IN","PAYMENT_OUT","CONTRACT","QUOTE_OVER_TEMPLATE","DISCOUNT",
  "OUTREACH_NEW","COMPLAINT","DISPUTE","LEGAL_TAX","PLATFORM_RISK","PII_PRIVACY",
  "GUARDRAIL_HIT","SECURITY_EVENT","BUDGET_BREACH","ERROR_RATE_BREACH","FIRST_THREE_ORDERS"]
ids = [c.get("id") for c in sw.get("categories", [])]
chk(len(ids) == 16 and ids == expected, "16 类情形齐全且顺序一致（实有 %d 类）" % len(ids))
for c in sw.get("categories", []):
    for k in ("trigger", "stop_action", "escalate_to", "record_to"):
        chk(bool(c.get(k)), "类别 %s 有字段 %s" % (c.get("id"), k))
chk(all(c.get("escalate_to") == "董事长" for c in sw.get("categories", [])), "全部 16 类找的都是董事长")
chk(sw.get("budget_line", {}).get("autonomous_spend_cny") == 0, "预算线：自主支出上限 0 元")
chk(sw.get("first_three_orders", {}).get("required_confirmations") == 3, "首 3 单须确认 3 单")
chk(sw.get("first_three_orders", {}).get("confirmed_count") == 0, "首 3 单已确认 0/3")
chk(env.get("opened_by") in (None, "董事长"), "自开拦截：开启人只能是董事长（本次为空，正常）")
print("RESULT: " + ("PASS" if not fails else "FAIL"))
sys.exit(1 if fails else 0)
PYEOF
