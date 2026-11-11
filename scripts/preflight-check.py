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
    if found:
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
    p1.add_argument("--kind", choices=["code", "model", "other"], default="other")
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
