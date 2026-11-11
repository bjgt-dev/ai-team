#!/usr/bin/env python3
"""check-run.py —— 情报增量扫 Harness：验收脚本（Phase 3）。
读 runs/<RUN_ID>/{result.json, collect.err, meta.json, record-tail.txt}，
逐项机器判定，写 check.json。退出码 0 = 全部通过；1 = 有 FAIL。
判定口径（见 SKILL.md 验收表）：
- 采集 JSON 有效且 mode == incremental；SKIPPED_OVERLOAD 判 DEFERRED（不算 FAIL）。
- sources 含 技能；new == 0 时记录未增长是正常的。
- new > 0 时：增量记录行数必须增长，且 record-tail 里有本轮的 ## HH:MM 小节。
- skills_appended 必须等于登记表第七节实际增加的行数。
- free_hits > 0 当且仅当当日有报警文件；free_hits == 0 时当日报警文件数不得增加。
- 源失败（collect.err / stdout 里 "source X failed"）必须逐条列出，不许静默。
用法：python3 scripts/check-run.py runs/<RUN_ID>
"""
import json
import os
import re
import sys

run_dir = sys.argv[1]
checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": detail})


def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def read(p):
    with open(p, encoding="utf-8", errors="ignore") as f:
        return f.read()


result_path = os.path.join(run_dir, "result.json")
meta_path = os.path.join(run_dir, "meta.json")
err_path = os.path.join(run_dir, "collect.err")
tail_path = os.path.join(run_dir, "record-tail.txt")

result = None
try:
    result = load_json(result_path)
    check("result_json_valid", True)
except Exception as e:
    check("result_json_valid", False, f"result.json 解析失败：{e}（可能是锁 SKIP 导致空输出）")

meta = load_json(meta_path) if os.path.exists(meta_path) else {}
if result is None:
    verdict = "FAIL"
else:
    mode = result.get("mode")
    if result.get("result") == "SKIPPED_OVERLOAD":
        check("not_overload_skipped", False, "过载保护跳过采集，本轮顺延（DEFERRED，不是 FAIL）")
        verdict = "DEFERRED"
    elif mode != "incremental":
        check("mode_incremental", False, f"mode={mode}")
        verdict = "FAIL"
    else:
        check("mode_incremental", True)
        sources = result.get("sources", {})
        check("source_skills_present", "技能" in sources,
              f"sources={sources}")
        new_n = result.get("new", -1)
        skill_new = result.get("skill_new", -1)
        check("counts_nonnegative", new_n >= 0 and skill_new >= 0,
              f"new={new_n} skill_new={skill_new}")
        if new_n > 0:
            grown = meta.get("record_lines_after", -1) > meta.get("record_lines_before", -1)
            check("record_grew", grown,
                  f"record {meta.get('record_lines_before')} -> {meta.get('record_lines_after')}")
            tail = read(tail_path) if os.path.exists(tail_path) else ""
            check("record_has_round_section", bool(re.search(r"^## \d{2}:\d{2} 增量", tail, re.M)),
                  "record-tail 里应有本轮 ## HH:MM 增量小节")
        else:
            check("record_grew", True, "new=0，无新内容，记录不增长是正常的")
            check("record_has_round_section", True, "new=0，无新小节是正常的")
        appended = result.get("skills_appended", 0 if new_n == 0 else -1)
        vii_delta = meta.get("registry_vii_after", -1) - meta.get("registry_vii_before", -1)
        check("skills_appended_matches_registry", appended == vii_delta,
              f"skills_appended={appended}（{'键缺失按 0 计' if 'skills_appended' not in result else 'result.json 有键'}）登记表第七节实际增加={vii_delta}")
        free_hits = result.get("free_hits", 0 if new_n == 0 else -1)
        alerts_before = set(meta.get("alert_files_before", []))
        alerts_after = set(meta.get("alert_files_after", []))
        new_alerts = alerts_after - alerts_before
        check("free_hits_matches_alert", (free_hits > 0) == (len(new_alerts) > 0),
              f"free_hits={free_hits}（{'键缺失按 0 计' if 'free_hits' not in result else 'result.json 有键'}）本轮新增报警文件={len(new_alerts)}")
        verdict = "PASS"

# 源失败必须显式列出（全量 stdout 与 stderr 都扫）
failures = []
for p in (os.path.join(run_dir, "result-full.txt"), err_path):
    if os.path.exists(p):
        failures += re.findall(r"source (\S+) failed: ([^\n]*)", read(p))
check("source_failures_listed", True,
      f"本轮源失败 {len(failures)} 项：" + ("；".join(f"{s}：{m}" for s, m in failures) if failures else "无"))

failed = [c for c in checks if not c["pass"]]
if verdict != "DEFERRED":
    verdict = "FAIL" if failed else "PASS"
out = {"run_dir": run_dir, "verdict": verdict, "checks": checks,
       "source_failures": [{"source": s, "message": m} for s, m in failures]}
with open(os.path.join(run_dir, "check.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1))
sys.exit(0 if verdict == "PASS" else 1)
