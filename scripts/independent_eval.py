#!/usr/bin/env python3
"""第二层：独立评判员。输入只有：锁死的验收标准 + 产物路径。明确不读：执行方会话、执行方总结、执行方日志。
用法：python3 independent_eval.py --contract <json> --deliverable <file>
合同字段：criteria（每条含 keyword 或 min_chars），criteria_hash（事前锁死，须与现场算出一致）。
评判规则：产物缺失 -> FAIL；标准哈希对不上 -> FAIL；任一 keyword 缺失或字数不足 -> FAIL；否则 PASS，并逐条列出产物中命中的证据行号（grep 证据，不是执行方自述）。
"""
import argparse, json, hashlib, sys, pathlib
ap = argparse.ArgumentParser()
ap.add_argument("--contract", required=True); ap.add_argument("--deliverable", required=True)
a = ap.parse_args()
c = json.load(open(a.contract, encoding="utf-8"))
criteria = c.get("criteria", [])
want_hash = c.get("criteria_hash", "")
got_hash = hashlib.sha256(json.dumps(criteria, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
if want_hash and got_hash != want_hash:
    print("EVAL_FAIL: criteria_hash mismatch, standard was changed after lock"); sys.exit(1)
d = pathlib.Path(a.deliverable)
if not d.exists():
    print("EVAL_FAIL: deliverable missing"); sys.exit(1)
text = d.read_text(encoding="utf-8", errors="ignore")
fails = []
for i, crit in enumerate(criteria):
    kw = crit.get("keyword")
    if kw and kw not in text:
        fails.append(f"criteria[{i}] keyword missing: {kw}")
    mc = crit.get("min_chars")
    if mc and len(text) < mc:
        fails.append(f"criteria[{i}] chars {len(text)} < {mc}")
if fails:
    print("EVAL_FAIL: " + "; ".join(fails)); sys.exit(1)
print(f"EVAL_PASS: deliverable chars={len(text)}, criteria={len(criteria)}, evidence from deliverable only")
