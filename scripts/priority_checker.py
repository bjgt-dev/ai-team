#!/usr/bin/env python3
"""优先级机制最小检查器（A 案 Day 0）：格式校验 + E1 逆序 + E2 主线空窗.

用法：
  python3 scripts/priority_checker.py            # 全量检查，结果写 state/priority/checker-out.json
  python3 scripts/priority_checker.py --quiet    # 同上，不打印摘要
退出码恒为 0（供 cron 使用）；红灯信息在输出 JSON 的 red 字段与 reasons 列表。
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta

import yaml

BASE = "/home/ubuntu/ai-team"
CONFIG = os.path.join(BASE, "state/priority/config.json")
LEVELS = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}
REVENUE = {"revenue_direct", "revenue_front"}
REQUIRED = ["id", "title", "source", "value_type", "irreversible_loss", "blocks_main",
            "blocks_count", "deadline", "status", "created_at", "last_progress", "sort_reason"]
VALUE_TYPES = {"stop_bleed", "revenue_direct", "revenue_front", "infra", "housekeeping"}
SOURCES = {"chairman", "self_patrol", "alert", "schedule"}
STATUSES = {"pending", "active", "waiting", "blocked", "done"}


def now():
    return datetime.now().astimezone()


def parse_ts(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None


def load_config():
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def load_table(cfg):
    with open(cfg["decision_table"], encoding="utf-8") as f:
        return yaml.safe_load(f)


def classify(card, table):
    vt = card.get("value_type")
    src = card.get("source")
    urg = card.get("urgency", "normal")
    if card.get("irreversible_loss") is True:
        level, rule = "P0", "irreversible"
    elif src == "chairman" and urg == "immediate":
        level, rule = "P0", "chairman-immediate"
    elif src == "chairman":
        if vt in REVENUE:
            level, rule = "P2", "chairman-other(revenue)"
        else:
            level, rule = "P1", "chairman-other"
    elif card.get("blocks_main") is True or int(card.get("blocks_count") or 0) >= 2:
        level, rule = "P1", "unblock"
    elif vt in REVENUE:
        level, rule = "P2", "revenue"
    elif vt == "infra":
        level, rule = "P3", "infra"
    else:
        level, rule = "P4", "rest"
    dl = parse_ts(card.get("deadline"))
    if dl and level != "P0":
        delta = dl - now()
        if timedelta(0) <= delta <= timedelta(hours=12):
            better = {v: k for k, v in LEVELS.items()}[max(LEVELS[level] - 1, 0)]
            level, rule = better, rule + "+deadline12h"
    return level, rule


def load_cards(ledger_dir):
    cards, errors = [], []
    for name in sorted(os.listdir(ledger_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(ledger_dir, name)
        try:
            with open(path, encoding="utf-8") as f:
                c = json.load(f)
        except Exception as e:
            errors.append({"file": name, "error": f"json: {type(e).__name__}"})
            continue
        c["_file"] = name
        cards.append(c)
    return cards, errors


def check_format(cards, table):
    errors = []
    for c in cards:
        fid = c.get("_file")
        for k in REQUIRED:
            if k not in c:
                errors.append({"file": fid, "field": k, "error": "missing"})
        if c.get("id") and not re.match(r"^[A-Za-z0-9._-]+$", str(c["id"])):
            errors.append({"file": fid, "field": "id", "error": "bad_charset"})
        if c.get("source") not in SOURCES:
            errors.append({"file": fid, "field": "source", "error": "bad_value"})
        if c.get("value_type") not in VALUE_TYPES:
            errors.append({"file": fid, "field": "value_type", "error": "bad_value"})
        if c.get("status") not in STATUSES:
            errors.append({"file": fid, "field": "status", "error": "bad_value"})
        if c.get("status") == "waiting" and not c.get("waiting_trigger"):
            errors.append({"file": fid, "field": "waiting_trigger", "error": "missing_for_waiting"})
        if not str(c.get("sort_reason") or "").strip():
            errors.append({"file": fid, "field": "sort_reason", "error": "empty(E5)"})
        expected, rule = classify(c, table)
        if c.get("priority") not in LEVELS:
            errors.append({"file": fid, "field": "priority", "error": "missing"})
        elif c.get("priority") != expected:
            errors.append({"file": fid, "field": "priority", "error": f"mismatch expected {expected} ({rule})"})
    return errors


def load_sort_log(path):
    rows = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
    return rows


def queue_empty():
    n = 0
    for stage in ("pending", "claimed"):
        root = os.path.join(BASE, "contracts", stage)
        if not os.path.isdir(root):
            continue
        for sub in os.listdir(root):
            p = os.path.join(root, sub)
            if os.path.isdir(p):
                n += sum(1 for x in os.listdir(p) if not x.startswith("."))
    return n == 0


def check_e1(cards, rows):
    """最近一轮里推进了低级别任务、而更高级别任务仍未处理 → E1。"""
    if not rows:
        return {"count": 0, "details": [], "note": "no sort log yet"}
    last = rows[-1]
    advanced = [a.get("id") for a in last.get("advanced", []) if isinstance(a, dict)]
    if not advanced:
        return {"count": 0, "details": [], "note": "last round has no advanced ids"}
    by_id = {c.get("id"): c for c in cards}
    details = []
    for aid in advanced:
        a = by_id.get(aid)
        if not a or a.get("status") == "done":
            continue
        alvl = LEVELS.get(a.get("priority"), 9)
        for c in cards:
            # E1 只把「可动作」任务算作未处理：waiting/blocked 已写明外部触发条件，不构成逆序
            if c.get("status") not in ("pending", "active") or c.get("id") == aid:
                continue
            clvl = LEVELS.get(c.get("priority"), 9)
            if clvl < alvl:
                details.append({"advanced": aid, "advanced_level": a.get("priority"),
                                "waiting": c.get("id"), "waiting_level": c.get("priority")})
    return {"count": len(details), "details": details}


def check_e2(cards, rows):
    """连续 8 轮零 P2 动作且工人有空槽 → E2。"""
    if len(rows) < 8:
        return {"triggered": False, "note": f"only {len(rows)} rounds logged (<8)"}
    by_id = {c.get("id"): c for c in cards}
    window = rows[-8:]
    counts = []
    for r in window:
        p2 = 0
        for a in r.get("advanced", []):
            if not isinstance(a, dict):
                continue
            lvl = a.get("priority") or (by_id.get(a.get("id"), {}) or {}).get("priority")
            if lvl == "P2":
                p2 += 1
        counts.append(p2)
    empty = queue_empty()
    return {"triggered": (sum(counts) == 0 and empty), "p2_per_round": counts,
            "worker_slot_empty": empty}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    table = load_table(cfg)
    cards, load_errors = load_cards(cfg["ledger_dir"])
    fmt = check_format(cards, table) + load_errors
    rows = load_sort_log(cfg["sort_log"])
    e1 = check_e1(cards, rows)
    e2 = check_e2(cards, rows)
    mode = cfg.get("priority_mode", "legacy")
    red_reasons = []
    if fmt:
        red_reasons.append(f"format_errors={len(fmt)}")
    if e1.get("count", 0) > 0:
        red_reasons.append(f"E1={e1['count']}")
    if e2.get("triggered"):
        red_reasons.append("E2=true")
    out = {
        "ts": now().isoformat(timespec="seconds"),
        "mode": mode,
        "config_tag": cfg.get("tag"),
        "cards_total": len(cards),
        "open_cards": sum(1 for c in cards if c.get("status") != "done"),
        "rounds_logged": len(rows),
        "format_errors": fmt,
        "E1": e1,
        "E2": e2,
        "red": bool(red_reasons),
        "reasons": red_reasons,
    }
    with open(cfg["checker_out"], "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    with open(os.path.join(os.path.dirname(cfg["checker_out"]), "checker-log.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": out["ts"], "mode": mode, "cards": len(cards),
                            "format_errors": len(fmt), "E1": e1.get("count", 0),
                            "E2": e2.get("triggered"), "red": out["red"]}, ensure_ascii=False) + "\n")
    if not args.quiet:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
