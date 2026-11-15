#!/usr/bin/env python3
"""preflight-check.py — 开工前强制预检门（AGENTS.md，2026-11-06 董事长令）。

四问缺一不可：①搜了哪些公开资源（地点+关键词）②找到了什么（名+星数实查+许可证）
③用还是造（能用直接用、能改改改用，只有确实没有或已验证不适用才能造）
④验证/验收计划是什么。低星（<1000★）按选品规则视为没有。
另合并且落地两条同源硬规：
  - 先搜后造，不许自以为是：设计任何机制前先搜高星现成方案再提炼。
  - 找代码去 GitHub、找模型去 HF：代码类须查 GitHub（星数实查），模型类须查
    Hugging Face（下载量与榜单实查）。searched 里无对应来源即 REFUSE。

用法：
  python3 preflight-check.py log --task <slug> --searched <...> --found <...>
      --decision <用/改/造...> [--kind code|model|other] [--ledger PATH]
      校验通过才记进台账（默认 state/preflight-ledger.jsonl）；校验不过拒绝记账。
  python3 preflight-check.py check --task <slug> [--ledger PATH]
      查该任务有无预检记录；无即 FAIL（不许开工）。
  python3 preflight-check.py audit [--ledger PATH]
      全表审计：缺字段、无来源、低星当有、决定词缺失均列为违规。
退出码：0=通过/有记录；1=拒绝/无记录/审计有违规；2=用法错。
"""
import argparse
import json
import re
import sys
from datetime import datetime

DEFAULT_LEDGER = "/home/ubuntu/ai-team/state/preflight-ledger.jsonl"
MIN_STARS = 1000
DECISION_WORDS = ("用", "改", "造")

# 四类公开资源（2026-11-15 董事长令，机制）：searched 须四类全有，缺一类即不过。
# 适用软件、硬件、未来一切项目。关键词为类别锚点，写“不适用”也须带上类别词。
FOUR_SOURCES = (
    ("厂商/官方", ("厂商", "官网", "官方", "规格书", "原理图", "datasheet")),
    ("商家售卖页", ("淘宝", "京东", "卖家", "商家", "售卖", "立创", "lcsc", "商品")),
    ("公开资料/开源", ("github", "开源", "公开资料", "数据手册", "拆解", "评测")),
    ("社区最佳实践", ("社区", "论坛", "reddit", "linux do", "最佳实践", "帖子")),
)

# 资料新鲜度（2026-11-15 董事长令，机制）：found 须注明资料更新时间或版本号。
_DATE_RE = re.compile(r"20\d\d(?:[-/年]\d{1,2})?(?:[-/月]\d{1,2})?")
_VERSION_RE = re.compile(r"(?:rev\.?\s*\d|v\d+\.\d|版本\s*\d|第\s*\d+\s*版)", re.IGNORECASE)


def _stars_in(text):
    """Return list of star counts found (e.g. '3125★', '42星', '3125 stars')."""
    out = []
    for m in re.finditer(r"(\d[\d,]*)\s*(?:★|星|stars?)", text):
        try:
            out.append(int(m.group(1).replace(",", "")))
        except ValueError:
            pass
    return out


def validate(task, searched, found, decision, kind="other"):
    """Return list of violation strings; empty means the preflight passes."""
    errors = []
    if not task or not task.strip():
        errors.append("缺 task slug")
    if not searched or len(searched.strip()) < 10:
        errors.append("四问①缺失：searched 须写清搜了哪些公开资源（地点+关键词）")
    if not found or len(found.strip()) < 5:
        errors.append("四问②缺失：found 须写清找到了什么（名+星数实查+许可证）")
    if not decision or len(decision.strip()) < 2:
        errors.append("四问③缺失：decision 须写清用/改/造及理由")
    if not searched:
        return errors
    low = searched.lower()
    has_hf = ("huggingface" in low or "hf.co" in low
              or re.search(r"\bhf\b", low) is not None)
    if kind == "code" and "github" not in low and "github.com" not in low:
        errors.append("代码类预检须查 GitHub：searched 无 GitHub")
    if kind == "model" and not has_hf:
        errors.append("模型类预检须查 Hugging Face：searched 无 Hugging Face")
    if kind == "other" and ("github" not in low and not has_hf):
        errors.append("预检须写清公开资源地点：searched 无 GitHub/Hugging Face 等来源")
    # 硬件更严（2026-11-15 董事长令，机制）：硬件涉费用，found 须含价格与现货/可购信息。
    if kind == "hardware" and found:
        _flow = found.lower()
        _price_words = ("¥", "$", "元", "价格", "成本", "报价", "售价")
        _stock_words = ("现货", "库存", "在售", "有货", "可购", "购买", "下单", "在库")
        if not any(w in _flow for w in _price_words):
            errors.append("硬件预检须含价格：found 未注明价格/成本（¥/$/元/价格/报价），硬件涉费用，不得无价开工")
        if not any(w in _flow for w in _stock_words):
            errors.append("硬件预检须含现货/可购信息：found 未注明现货/库存/在售（现货/库存/在售/有货），买不到等于没有")
    # 四类公开资源全查（2026-11-15 机制）：searched 缺任一类即不过。
    for cat_name, keywords in FOUR_SOURCES:
        if not any(kw in low for kw in keywords):
            errors.append("四类公开资源缺失：searched 未查 %s（须含厂商/官方、商家售卖页、公开资料/开源、社区最佳实践四类）" % cat_name)
    if found:
        # 资料新鲜度（2026-11-15 机制）：found 须注明资料更新时间或版本号。
        if not _DATE_RE.search(found) and not _VERSION_RE.search(found):
            errors.append("资料新鲜度缺失：found 须注明资料的更新时间或版本号（如 2026-11-15、Rev 1.10、v3.2.209），只给链接不给时间等于没核实")
        # 实时性（2026-11-15 机制）：别拿过时的信息。found 里最新的年份须 ≥2025。
        _years = [int(y) for y in re.findall(r"20\d\d", found)]
        if _years and max(_years) < 2025:
            errors.append("资料过时：found 里最新年份为 %d（早于 2025），请用最新资料重新核实" % max(_years))
        # 讨论热度（2026-11-15 机制）：found 须注明资料是否仍被积极讨论/维护。
        _heat_words = ("最近", "活跃", "热度", "讨论", "更新", "浏览", "评论",
                       "stars", "commits", "issues", "forks", "播放", "点赞")
        _flow = found.lower()
        if not any(w in _flow for w in _heat_words):
            errors.append("讨论热度缺失：found 未注明资料的讨论热度/活跃度（最近更新、讨论、星数、浏览等），无法判断资料是否仍被关注")
        stars = _stars_in(found)
        # 星数/下载量实查只对"用/改"决定强制：决定"造"时 found 本就应是"无现成"，
        # 无东西可标星，强制要星数会把 honest 的"造"也拒掉。
        if kind in ("code", "model") and decision and decision.strip().startswith(("用", "改")) \
                and not stars and "下载" not in found and "downloads" not in found.lower():
            errors.append("四问②缺失：found 无星数/下载量实查数字")
        low_stars = [s for s in stars if s < MIN_STARS]
        if low_stars and decision:
            d = decision.strip()
            if d.startswith(("用", "改")):
                errors.append("低星 %s★（<%d★）按选品规则视为没有，不得以'用/改'为决定"
                              % (low_stars[0], MIN_STARS))
    if decision:
        d = decision.strip()
        if not d.startswith(DECISION_WORDS):
            errors.append("四问③缺失：decision 须以 用/改/造 开头")
        elif d.startswith("造") and "无现成" not in d and "不适用" not in d and "没有" not in d:
            errors.append("决定'造'须写清理由：确实没有或已验证不适用")
    return errors


def _load_ledger(path):
    recs = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    recs.append(json.loads(line))
    except OSError:
        return recs
    except ValueError:
        return recs
    return recs


def cmd_log(args):
    errors = validate(args.task, args.searched, args.found, args.decision, args.kind)
    if errors:
        print("PREFLIGHT REFUSED task=%s" % args.task)
        for e in errors:
            print("  - %s" % e)
        return 1
    rec = {"ts": datetime.now().isoformat(timespec="seconds"),
           "task": args.task, "searched": args.searched, "found": args.found,
           "decision": args.decision, "kind": args.kind}
    with open(args.ledger, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("PREFLIGHT LOGGED task=%s" % args.task)
    return 0


def cmd_check(args):
    recs = _load_ledger(args.ledger)
    if any(r.get("task") == args.task for r in recs):
        print("PREFLIGHT OK: record exists for %s" % args.task)
        return 0
    print("PREFLIGHT FAIL: no preflight for %s - do not start" % args.task)
    return 1


def cmd_audit(args):
    recs = _load_ledger(args.ledger)
    problems = []
    for i, r in enumerate(recs):
        errs = validate(r.get("task", ""), r.get("searched", ""),
                        r.get("found", ""), r.get("decision", ""),
                        r.get("kind", "other"))
        for e in errs:
            problems.append("行 %d task=%s：%s" % (i + 1, r.get("task"), e))
    print("预检台账：共 %d 条，违规 %d 条" % (len(recs), len(problems)))
    for p in problems:
        print("  - %s" % p)
    return 1 if problems else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("log")
    p1.add_argument("--task", required=True)
    p1.add_argument("--searched", required=True)
    p1.add_argument("--found", required=True)
    p1.add_argument("--decision", required=True)
    p1.add_argument("--kind", choices=["code", "model", "hardware", "other"], default="other")
    p2 = sub.add_parser("check")
    p2.add_argument("--task", required=True)
    sub.add_parser("audit")
    args = ap.parse_args()
    if args.cmd == "log":
        return cmd_log(args)
    if args.cmd == "check":
        return cmd_check(args)
    return cmd_audit(args)


if __name__ == "__main__":
    sys.exit(main())
