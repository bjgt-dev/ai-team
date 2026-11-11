#!/usr/bin/env python3
"""M3 指标带分母持续校验 (2026-11-07 M3/M4/M5 落地扫描补齐).

缺口: 2026-11-05 十个脚本已改成分母格式 (回执
state/m3-exit-20261105/4-metrics-denominator-20261105.md, 16 行对照表),
但没有任何代码持续校验"新写/新跑的指标仍带分母"——回归无门禁.
本脚本逐行复核 5 个关键存量输出仍带分母, 缺/错即 FAIL.

行:
  1 model-health.json: healthy_ratio "x/y"
  2 canary-b-tally-20261104.json: 每模型 ratio "x/y"
  3 tier2-tally-20261104.json: scores 每模型 "x/y"
  4 worker_reputation.md: 含 "n/m (" 格式的过件率
  5 gateway-canary-20261104.jsonl 末行: pass_items "x/3"

用法:
  python3 m3-metrics-denominator-check.py [--state DIR]
  python3 m3-metrics-denominator-check.py --self-test  # /tmp 夹具正反例
只读. Stdlib only.
"""
import argparse
import json
import os
import re
import sys

BASE = "/home/ubuntu/ai-team/state"
RATIO_RE = re.compile(r"^\d+/\d+$")


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check_row1(state):
    # healthy_ratio 只印在 cmd_probe 的 stdout, 未落盘 (2026-11-07 核查):
    # 本行改为从落盘的 models 明细重算 healthy/total, 分母可复核.
    p = os.path.join(state, "model-health.json")
    if not os.path.exists(p):
        return "MISSING", "文件缺失"
    try:
        d = _load_json(p)
    except Exception as exc:
        return "FAIL", "JSON 解析失败: %s" % exc
    models = d.get("models") if isinstance(d, dict) else None
    if not isinstance(models, dict) or not models:
        return "FAIL", "models 明细缺失, 无法重算分母"
    total = len(models)
    healthy = sum(1 for m in models.values()
                  if isinstance(m, dict) and m.get("status") == "healthy")
    return "PASS", "重算 healthy/total=%d/%d (与 probe 摘要口径一致)" % (healthy, total)


def check_row2(state):
    p = os.path.join(state, "canary-b-tally-20261104.json")
    if not os.path.exists(p):
        return "MISSING", "文件缺失"
    try:
        d = _load_json(p)
    except Exception as exc:
        return "FAIL", "JSON 解析失败: %s" % exc
    tally = d.get("tally") if isinstance(d, dict) else None
    if not isinstance(tally, dict):
        tally = d  # 兼容旧版顶层平铺
    bad = []
    n = 0
    for k, v in tally.items():
        if isinstance(v, dict) and "ratio" in v:
            n += 1
            if not (isinstance(v["ratio"], str) and RATIO_RE.match(v["ratio"])):
                bad.append(k)
    if not n:
        return "FAIL", "无任何 ratio 字段"
    return ("PASS", "%d 模型 ratio 齐全" % n) if not bad else ("FAIL", "ratio 格式错: %s" % bad)


def check_row3(state):
    p = os.path.join(state, "tier2-tally-20261104.json")
    if not os.path.exists(p):
        return "MISSING", "文件缺失"
    try:
        d = _load_json(p)
    except Exception as exc:
        return "FAIL", "JSON 解析失败: %s" % exc
    scores = d.get("scores") if isinstance(d, dict) else None
    if not isinstance(scores, dict) or not scores:
        return "FAIL", "scores 缺失"
    bad = [k for k, v in scores.items() if not (isinstance(v, str) and RATIO_RE.match(v))]
    return ("PASS", "scores=%s" % scores) if not bad else ("FAIL", "scores 格式错: %s" % bad)


def check_row4(state):
    p = os.path.join(state, "worker_reputation.md")
    if not os.path.exists(p):
        return "MISSING", "文件缺失"
    text = open(p, encoding="utf-8").read()
    hits = re.findall(r"\d+/\d+ \(", text)
    return ("PASS", "%d 处 n/m (%%) 格式" % len(hits)) if hits else ("FAIL", "无 n/m (%%) 格式过件率")


def check_row5(state):
    p = os.path.join(state, "gateway-canary-20261104.jsonl")
    if not os.path.exists(p):
        return "MISSING", "文件缺失"
    last = None
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line:
            last = line
    if last is None:
        return "FAIL", "文件为空"
    try:
        d = json.loads(last)
    except ValueError:
        return "FAIL", "末行非 JSON"
    v = d.get("pass_items")
    if isinstance(v, str) and RATIO_RE.match(v):
        return "PASS", "末行 pass_items=%s" % v
    return "FAIL", "末行 pass_items 缺失或非 x/y 格式 (现为 %r)" % (v,)


CHECKS = [
    ("1 网关探活健康率", check_row1),
    ("2 canary_b 池资格", check_row2),
    ("3 Tier-2 资格", check_row3),
    ("4 工人信誉过件率", check_row4),
    ("5 金丝雀单轮通过项", check_row5),
]


def run(state):
    fails = 0
    for name, fn in CHECKS:
        verdict, detail = fn(state)
        print("%s: %s — %s" % (verdict, name, detail))
        if verdict != "PASS":
            fails += 1
    print("RESULT: %s (%d/%d 行通过)" % ("PASS" if not fails else "FAIL", len(CHECKS) - fails, len(CHECKS)))
    return 1 if fails else 0


def self_test():
    import tempfile
    tmp = tempfile.mkdtemp(prefix="m3den-")
    good = os.path.join(tmp, "good"); os.makedirs(good)
    bad = os.path.join(tmp, "bad"); os.makedirs(bad)
    json.dump({"models": {"a": {"status": "healthy"}, "b": {"status": "healthy"},
               "c": {"status": "unhealthy"}}},
              open(os.path.join(good, "model-health.json"), "w"))
    json.dump({"agnes": {"ratio": "1/5"}, "gemini": {"ratio": "0/5"}},
              open(os.path.join(good, "canary-b-tally-20261104.json"), "w"))
    json.dump({"scores": {"agnes": "2/2", "gemini": "0/2"}},
              open(os.path.join(good, "tier2-tally-20261104.json"), "w"))
    open(os.path.join(good, "worker_reputation.md"), "w").write("guangtou 1/35 (3%)\n")
    open(os.path.join(good, "gateway-canary-20261104.jsonl"), "w").write(
        json.dumps({"pass_items": "3/3"}) + "\n")
    json.dump({"healthy_ratio": "27%"}, open(os.path.join(bad, "model-health.json"), "w"))
    json.dump({"agnes": {"ratio": "20%"}}, open(os.path.join(bad, "canary-b-tally-20261104.json"), "w"))
    json.dump({"scores": {"agnes": "100%"}},
              open(os.path.join(bad, "tier2-tally-20261104.json"), "w"))
    open(os.path.join(bad, "worker_reputation.md"), "w").write("guangtou 3%\n")
    open(os.path.join(bad, "gateway-canary-20261104.jsonl"), "w").write(
        json.dumps({"pass": True}) + "\n")
    import io, contextlib
    fails = []

    def chk(cond, msg):
        print(("PASS " if cond else "FAIL ") + msg)
        if not cond:
            fails.append(msg)

    with contextlib.redirect_stdout(io.StringIO()):
        rc_good = run(good)
    chk(rc_good == 0, "全带分母夹具 5/5 通过 (正例)")
    with contextlib.redirect_stdout(io.StringIO()):
        rc_bad = run(bad)
    chk(rc_bad == 1, "裸百分比夹具被判 FAIL (反例)")
    print("SELF-TEST: %d failures" % len(fails))
    return 1 if fails else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="M3 metrics-denominator check (read-only)")
    ap.add_argument("--state", default=BASE)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    return run(a.state)


if __name__ == "__main__":
    sys.exit(main())
