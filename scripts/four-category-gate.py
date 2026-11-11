#!/usr/bin/env python3
"""Four-category gate: decide whether a decision-queue item needs the
chairman, before it is reported to him. The four categories are:
  1. money (花钱) - spending, payment, purchase, subscription
  2. external publication (对外发布) - publish, launch, public, external
  3. irreversible (不可逆) - delete, destroy, irreversible, cannot rollback
  4. chairman-only input (只有董事长能给的输入) - username, password,
     select, decide, approve, scan code, verification code, login
Matching is conservative: any keyword hit means chairman is needed.
A miss means the item is self-decidable (technical, reversible).
Usage: needs_chairman(item) -> (bool, reason)
  item: dict with task_id / summary / type / options (list of dicts
  with desc/cost_risk) - any of these fields are scanned.
"""
import re

# Category keyword lists (Chinese + English). Conservative: a hit in any
# field means the chairman must decide. When in doubt, flag as chairman.
CATEGORIES = [
    ("money", [
        "花钱", "付费", "充值", "购买", "订阅", "成本", "金额", "价格",
        "扣费", "账单", "付款", "支付", "买", "订购",
        "buy", "purchase", "subscribe", "subscription", "payment",
        "pay", "cost", "price", "charge", "billing", "$", "¥", "￥",
    ]),
    ("external_publication", [
        "对外发布", "对外联系", "发布", "上线", "公开", "对外",
        "publish", "release", "public", "external", "launch",
        "go-live", "go live", "announce",
    ]),
    ("irreversible", [
        "删除", "销毁", "不可逆", "无法回滚", "无法恢复",
        "不可恢复", "清除数据", "删库",
        "delete", "destroy", "irreversible", "permanent",
        "cannot rollback", "cannot be undone", "drop table",
        "drop database", "rm -rf",
    ]),
    ("chairman_only_input", [
        "用户名", "密码", "点选", "拍板", "定名单", "扫码", "验证码",
        "交互登录", "董事长", "董事长定", "等董事长", "请董事长",
        "username", "password", "select", "decide", "approve",
        "verification code", "scan code", "login", "sign-in",
        "chairman",
    ]),
]


def _item_text(item):
    """Concatenate all searchable fields of a decision-queue item."""
    parts = []
    if isinstance(item, dict):
        for key in ("task_id", "summary", "type", "five_step_path",
                    "deadline_hint"):
            v = item.get(key)
            if v:
                parts.append(str(v))
        for opt in item.get("options", []) or []:
            if isinstance(opt, dict):
                for k in ("id", "desc", "cost_risk"):
                    if opt.get(k):
                        parts.append(str(opt[k]))
            elif opt:
                parts.append(str(opt))
    elif item:
        parts.append(str(item))
    return " ".join(parts)


def _keyword_hit(text_low, kw):
    """Match a keyword. ASCII (English) keywords use word boundaries so
    'pay' does not match 'payload'; Chinese keywords use substring."""
    k = kw.lower()
    if all(ord(c) < 128 for c in k):
        # English/ASCII: word-boundary match (escape regex chars).
        return re.search(r"\b" + re.escape(k) + r"\b", text_low) is not None
    return k in text_low


def needs_chairman(item):
    """Return (needs_chairman: bool, reason: str).
    True + category name when any four-category keyword matches.
    False + 'auto: ...' when no keyword matches (self-decidable)."""
    text = _item_text(item)
    low = text.lower()
    for name, keywords in CATEGORIES:
        for kw in keywords:
            if _keyword_hit(low, kw):
                return (True, name + ":" + kw)
    return (False, "auto: no four-category keyword; technical/reversible, self-decidable")


if __name__ == "__main__":
    import json
    import sys
    # Quick self-test: read JSON items from stdin or args, print verdicts.
    items = []
    if len(sys.argv) > 1:
        for a in sys.argv[1:]:
            items.append(json.loads(a))
    else:
        for line in sys.stdin:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    for it in items:
        need, reason = needs_chairman(it)
        print(json.dumps({"needs_chairman": need, "reason": reason,
                          "id": it.get("id", "")}, ensure_ascii=False))
