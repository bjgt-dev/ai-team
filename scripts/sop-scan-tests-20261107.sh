#!/bin/bash
# SOP-scan 2026-11-07 test battery. Runs on hub. Prints PASS/FAIL per case.
# Exit 0 only if every case matches its expected result.
S=/home/ubuntu/ai-team/scripts
T=/tmp/sopscan-test
rm -rf $T && mkdir -p $T/good $T/skel $T/plan $T/review
FAIL=0
ck() { # ck <label> <expected_rc> <actual_rc>
  if [ "$2" = "$3" ]; then echo "PASS: $1 (rc=$3)"; else echo "FAIL: $1 expected rc=$2 got rc=$3"; FAIL=1; fi
}
ck_contains() { # ck_contains <label> <file> <pattern>
  if grep -q "$3" "$2"; then echo "PASS: $1"; else echo "FAIL: $1 (pattern '$3' not in $2)"; FAIL=1; fi
}

# ---------- fixtures: good code deliverable ----------
cat > $T/good/add_one.py <<'EOF'
def add(a, b):
    """Add two numbers."""
    return a + b
EOF
cat > $T/good/test_add_one.py <<'EOF'
from add_one import add
def test_add():
    assert add(2, 3) == 5
EOF
cat > $T/good-contract.json <<'EOF'
{"task_id": "gates-test-good", "task_type": "code", "workdir": "~/code-agent-pilot/workdir/gates-test", "output_path": "/tmp/sopscan-test/good/add_one.py", "tests": ["test_add_one.py"]}
EOF
# fake-3: pass-only skeleton
cat > $T/skel/audit.py <<'EOF'
def audit():
    pass
def main():
    pass
EOF
cat > $T/skel/test_audit.py <<'EOF'
from audit import audit
def test_audit_runs():
    audit()
EOF
cat > $T/skel-contract.json <<'EOF'
{"task_id": "gates-test-skel", "task_type": "code", "workdir": "~/code-agent-pilot/workdir/gates-test", "output_path": "/tmp/sopscan-test/skel/audit.py", "tests": ["test_audit.py"]}
EOF
# fake-1: pytest-only stub artifact
printf 'pytest_exit=0\n1 passed in 0.00s' > $T/fake1.bin
cat > $T/fake1-contract.json <<'EOF'
{"task_id": "gates-test-fake1", "task_type": "code", "workdir": "~/code-agent-pilot/workdir/gates-test", "output_path": "/tmp/sopscan-test/good/add_one.py"}
EOF
# fake-2: llm_doc echo
cat > $T/echo.md <<'EOF'
# hello world input text
EOF
cat > $T/fake2-contract.json <<'EOF'
{"task_id": "gates-test-fake2", "task_type": "llm_doc", "output_path": "state/deliverables/gates-test-fake2.md", "input": {"content": "# hello world input text\n"}}
EOF
# bundle of the good deliverable (production format: tar.gz, see pull_once.py _bundle_workdir)
python3 - $T/good $T/good.bundle <<'EOF'
import sys, json, hashlib, os, io, tarfile
d, out = sys.argv[1], sys.argv[2]
files = []
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as tf:
    for name in sorted(os.listdir(d)):
        data = open(os.path.join(d, name), "rb").read()
        files.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        ti = tarfile.TarInfo(name)
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
tar_bytes = buf.getvalue()
man = json.dumps({"format": "PULLONCE-BUNDLE-V1", "files": files, "bundle_bytes": len(tar_bytes), "bundle_sha256": hashlib.sha256(tar_bytes).hexdigest()}, sort_keys=True).encode()
open(out, "wb").write(b"PULLONCE-BUNDLE-V1\n" + man + b"\n" + tar_bytes)
print("bundle written (tar.gz production format)")
EOF

echo "== 1. verifier-gates-234 =="
python3 $S/verifier-gates-234-20261107.py --artifact $T/good --contract $T/good-contract.json > $T/o1.txt 2>&1; ck "gates good dir -> PASS" 0 $?
python3 $S/verifier-gates-234-20261107.py --artifact $T/good.bundle --contract $T/good-contract.json > $T/o2.txt 2>&1; ck "gates good bundle -> PASS" 0 $?
python3 $S/verifier-gates-234-20261107.py --artifact $T/skel --contract $T/skel-contract.json > $T/o3.txt 2>&1; ck "gates fake-3 skeleton -> REJECT" 1 $?
ck_contains "fake-3 rejected by gate2+gate4" $T/o3.txt "gate2: REJECT"
python3 $S/verifier-gates-234-20261107.py --artifact $T/fake1.bin --contract $T/fake1-contract.json > $T/o4.txt 2>&1; ck "gates fake-1 stub -> REJECT" 1 $?
python3 $S/verifier-gates-234-20261107.py --artifact $T/echo.md --contract $T/fake2-contract.json > $T/o5.txt 2>&1; ck "gates fake-2 echo -> REJECT" 1 $?
ck_contains "fake-2 rejected by gate5" $T/o5.txt "gate5: REJECT"
python3 $S/verifier-gates-234-20261107.py --artifact /tmp/does-not-exist.bin --contract $T/good-contract.json > $T/o6.txt 2>&1; ck "gates missing artifact -> TOOL-ERROR rc2" 2 $?

echo "== 2. taskcontract_gate fuzzy (Rule 8 fix) =="
python3 - <<'EOF'
import json
base = {"task_id": "gate-fuzzy-test", "task_type": "code", "workdir": "~/code-agent-pilot/workdir/x", "output_path": "/tmp/x.py", "input": {"cmd": "pytest"}, "evidence": "e", "pass_line": "p", "fail_action": "f", "verifier": "v", "forbidden": "n", "five_part": {"goal": "g", "constraint": "c", "stop_when": "s", "must_not": "m", "report": "r"}}
c1 = dict(base, fuzzy=True, variants=[])
c2 = dict(base, fuzzy=True, variants=[{"name": "A"}, {"name": "B"}])
c3 = dict(base, variants=[])
json.dump(c1, open("/tmp/sopscan-test/fuzzy-empty.json", "w"))
json.dump(c2, open("/tmp/sopscan-test/fuzzy-ok.json", "w"))
json.dump(c3, open("/tmp/sopscan-test/crisp-empty.json", "w"))
EOF
python3 $S/taskcontract_gate.py --validate $T/fuzzy-empty.json > $T/o7.txt 2>&1; ck "gate fuzzy + variants [] -> INVALID" 1 $?
python3 $S/taskcontract_gate.py --validate $T/fuzzy-ok.json > $T/o8.txt 2>&1; ck "gate fuzzy + 2 variants -> VALID" 0 $?
python3 $S/taskcontract_gate.py --validate $T/crisp-empty.json > $T/o9.txt 2>&1; ck "gate crisp + variants [] -> VALID" 0 $?

echo "== 3. model-tier-check (Rule 1) =="
python3 - <<'EOF'
import json
json.dump({"task_id": "m1", "task_type": "code", "fuzzy": True, "input": {}}, open("/tmp/sopscan-test/m1.json", "w"))
json.dump({"task_id": "m2", "task_type": "code", "fuzzy": True, "input": {"model_tier": "tier1"}}, open("/tmp/sopscan-test/m2.json", "w"))
json.dump({"task_id": "m3", "task_type": "code", "mechanical": True, "input": {"model_tier": "small"}}, open("/tmp/sopscan-test/m3.json", "w"))
json.dump({"task_id": "m4", "task_type": "code", "fuzzy": True, "input": {"model_tier": "small"}}, open("/tmp/sopscan-test/m4.json", "w"))
EOF
python3 $S/model-tier-check-20261107.py $T/m1.json > $T/o10.txt 2>&1; ck "model fuzzy no tier -> FAIL" 1 $?
python3 $S/model-tier-check-20261107.py $T/m2.json > $T/o11.txt 2>&1; ck "model fuzzy tier1 -> PASS" 0 $?
python3 $S/model-tier-check-20261107.py $T/m3.json > $T/o12.txt 2>&1; ck "model mechanical small -> PASS" 0 $?
python3 $S/model-tier-check-20261107.py $T/m4.json > $T/o13.txt 2>&1; ck "model fuzzy small -> FAIL" 1 $?

echo "== 4. plan-validate =="
cat > $T/plan/good-code-plan.md <<'EOF'
# 代码任务 Plan 模板
## Brief（任务简述）
- 一句话：给 verifier 接上 2/3/4 号门。
- 背景：SOP 有，代码无。
- **不做什么（边界）：** 不改派队逻辑，不动生产数据。
## Outline（实现大纲）
1. 改 scripts/verifier-gates-234-20261107.py，加 AST 检查，预期 gate2 能拒骨架。
2. 改 scripts/c2-verifier-20261104.py，接线，预期 py_compile 通过。
## Theme（技术选型与约束）
- 语言 / 运行时：python3
## Assets（输入资产）
| 资产 | 枢纽路径 | worker 相对路径 | 已确认 |
| spec | state/cron-inventory.md | — | x |
## Contract（合同字段）
- task_id / slug：gates-test
- workdir（worker 相对路径）：~/code-agent-pilot/workdir/gates-test
- output_path：state/cron-inventory.md
## Tests（锁定测试清单）
| 测试 | 断言 | 预期值来源 |
| test_gate2 | 骨架被拒 | five-gates doc |
## Review（评审要点）
- Standards 轴：无
## Acceptance（什么叫 DONE）
- [ ] pytest exit 0
- [ ] 产物两份一致
- [ ] 产物 ≥500B
- [ ] verifier 日志行 DONE
- [ ] 评审两轴无硬违反
- [ ] 证据链齐
## Risks & Rollback（风险与回滚）
| 风险 | 坏了停在哪 | 回滚动作 |
| 依赖缺失 | provision 失败 | 补依赖 |
## Timebox（时间盒）
- 本任务时限：60 分钟
- 分段汇报点：每完成一步报一次
EOF
python3 $S/plan-validate-20261107.py code-plan $T/plan/good-code-plan.md > $T/o14.txt 2>&1; ck "plan good code-plan -> PASS" 0 $?
cp /tmp/sop-scan/sops/code-plan-template.md $T/plan/bad-code-plan.md 2>/dev/null || cp ~/ai-team/docs/sops/code-plan-template.md $T/plan/bad-code-plan.md
python3 $S/plan-validate-20261107.py code-plan $T/plan/bad-code-plan.md > $T/o15.txt 2>&1; ck "plan raw template -> FAIL" 1 $?
cat > $T/plan/good-article.md <<'EOF'
# Plan
## Brief
- 目标读者：工程师
- 文章类型：longform
- 信息保留比例：100%
- 必须保留的信息：
  - 第一章结论（source.md 第 1 段）
- 可删减的信息：
  - 重复的例子（重复）
- 语气：克制分析
## Outline
- Hero：标题
1. 第一章
   - 保留信息：第一章结论（source.md 第 1 段）
   - 需要的组件：正文
   - 是否需要 Raw：否
2. 第二章
   - 保留信息：第二章数据
   - 需要的组件：正文 + Table
   - 是否需要 Raw：是，服务第二章论点、目的为提供证据
- 结尾方式：Conclusion
## Theme
- 选定主题：tufte
- 理由：去装化
## Assets
- 策略：none
- 一句话说明：去装化一致。
EOF
python3 $S/plan-validate-20261107.py article-plan $T/plan/good-article.md > $T/o16.txt 2>&1; ck "plan good article-plan -> PASS" 0 $?
cat > $T/plan/bad-article.md <<'EOF'
# Plan
## Brief
- 目标读者：工程师
## Outline
1. 第一章
   - 保留信息：东西
3. 第三章
   - 保留信息：东西
## Theme
- 选定主题：tufte
## Assets
- 策略：none
EOF
python3 $S/plan-validate-20261107.py article-plan $T/plan/bad-article.md > $T/o17.txt 2>&1; ck "plan bad article-plan (numbering) -> FAIL" 1 $?

echo "== 5. preflight-gate (parallel landing 17:58, verified here) =="
LEDGER=$T/preflight.jsonl
python3 $S/preflight-gate-20261107.py --self-test > $T/o18.txt 2>&1; ck "preflight self-test -> ALL PASS" 0 $?
python3 $S/preflight-gate-20261107.py --task t1 --record --q1 "searched github gates" --q2 "found X 2000 stars MIT" --q3 "use X directly" --q4 "pytest green" --queue3 --ledger $LEDGER > $T/o19.txt 2>&1; ck "preflight record -> ok" 0 $?
python3 $S/preflight-gate-20261107.py --task t1 --check --ledger $LEDGER > $T/o20.txt 2>&1; ck "preflight check t1 -> PASS" 0 $?
python3 $S/preflight-gate-20261107.py --task t-none --check --ledger $LEDGER > $T/o21.txt 2>&1; ck "preflight check unknown task -> FAIL" 1 $?
python3 $S/preflight-gate-20261107.py --task t1 --check-queue3 --ledger $LEDGER > $T/o22.txt 2>&1; ck "preflight queue3 all true -> PASS" 0 $?

echo "== 6. link-research-registry =="
REG=$T/links.jsonl
python3 $S/link-research-registry-20261107.py --registry $REG add --url https://example.com/a --source chairman --relevance commercial --stars 5000 > $T/o23.txt 2>&1; ck "link add commercial -> ok" 0 $?
python3 $S/link-research-registry-20261107.py --registry $REG add --url https://example.com/b --source intel --relevance none > $T/o24.txt 2>&1; ck "link add irrelevant -> not-doing" 0 $?
python3 $S/link-research-registry-20261107.py --registry $REG settle --url https://example.com/a --state verified --evidence $T/good/add_one.py > $T/o25.txt 2>&1; ck "link settle verified w/ evidence -> ok" 0 $?
python3 $S/link-research-registry-20261107.py --registry $REG settle --url https://example.com/b --state verified > $T/o26.txt 2>&1; ck "link settle verified w/o evidence -> TOOL-ERROR" 2 $?
python3 $S/link-research-registry-20261107.py --registry $REG audit > $T/o27.txt 2>&1; ck "link audit clean -> PASS" 0 $?
python3 $S/link-research-registry-20261107.py --registry $REG add --url https://example.com/c --source intel --relevance infra > $T/o28.txt 2>&1
python3 - $REG <<'EOF'
import json, sys
p = sys.argv[1]
rows = [json.loads(l) for l in open(p)]
for r in rows:
    if r["url"] == "https://example.com/c":
        r["next_deadline"] = "2020-01-01T00:00:00Z"
open(p, "w").write("\n".join(json.dumps(r) for r in rows) + "\n")
EOF
python3 $S/link-research-registry-20261107.py --registry $REG audit > $T/o29.txt 2>&1; ck "link audit past deadline -> FAIL" 1 $?

echo "== 7. skill-blindtest =="
cat > $T/fake-runner.sh <<'EOF'
#!/bin/bash
if [ "$BLINDTEST_ARM" = "experiment" ]; then echo '{"compliant": true, "seconds": 5.0, "tokens": 100, "errors": 0}';
else echo '{"compliant": false, "seconds": 9.0, "tokens": 200, "errors": 1}'; fi
EOF
chmod +x $T/fake-runner.sh
cat > $T/spec.json <<EOF
{"task": "按约定加一个新 API 端点", "success": "端点可调用且遵守约定", "knowledge_files": ["$T/good/add_one.py"], "trials": 3, "runner": "$T/fake-runner.sh", "quarter": "2026q4-test"}
EOF
python3 $S/skill-blindtest-2026q4.py --spec $T/spec.json --out-dir $T > $T/o30.txt 2>&1; ck "blindtest run -> ok" 0 $?
ck_contains "blindtest verdict effective" $T/skill-blindtest-2026q4-test-REPORT.md "effective"
python3 $S/skill-blindtest-2026q4.py --spec /tmp/no-such-spec.json --out-dir $T > $T/o31.txt 2>&1; ck "blindtest bad spec -> TOOL-ERROR" 2 $?

echo "== 8. task-trace (Rule 13) =="
python3 $S/task-trace-20261107.py pipeline-canary-20261106d-202611061439 > $T/o32.txt 2>&1; ck "trace real DONE task -> found" 0 $?
ck_contains "trace shows contract" $T/o32.txt "contract:"
python3 $S/task-trace-20261107.py no-such-task-xyz-999 > $T/o33.txt 2>&1; ck "trace nonexistent -> NOT FOUND rc1" 1 $?

echo "== 9. cron-inventory-check (Rule 11) =="
python3 $S/cron-inventory-check-20261107.py generate --out $T/cron-inv.md > $T/o34.txt 2>&1; ck "cron generate -> ok" 0 $?
ck_contains "cron inventory lists health_3q" $T/cron-inv.md "health_3q"
python3 $S/cron-inventory-check-20261107.py check --inventory $T/cron-inv.md > $T/o35.txt 2>&1; ck "cron check live crontab -> PASS" 0 $?
printf '0 0 * * * python3 /home/ubuntu/ai-team/scripts/does-not-exist-xyz.py\n' > $T/fake-cron.txt
python3 $S/cron-inventory-check-20261107.py check --crontab $T/fake-cron.txt --inventory $T/cron-inv.md > $T/o36.txt 2>&1; ck "cron check missing script -> FAIL" 1 $?

echo "== 10. blast-radius + authorship (Rules 14/15, config-driven) =="
python3 $S/blast-radius-gate-20261107.py some/file.py > $T/o37.txt 2>&1; ck "blast no config -> NOT-CONFIGURED rc0" 0 $?
echo '{"paths": ["/home/ubuntu/ai-team/scripts/c2-verifier-20261104.py"], "categories": ["payment"]}' > $T/blast.json
python3 $S/blast-radius-gate-20261107.py --config $T/blast.json /home/ubuntu/ai-team/scripts/c2-verifier-20261104.py > $T/o38.txt 2>&1; ck "blast configured match -> HUMAN REVIEW rc2" 2 $?
python3 $S/blast-radius-gate-20261107.py --config $T/blast.json docs/notes.md > $T/o39.txt 2>&1; ck "blast configured no match -> PASS" 0 $?
python3 $S/authorship-boundary-20261107.py --kind tech-doc > $T/o40.txt 2>&1; ck "authorship no config -> NOT-CONFIGURED rc0" 0 $?
echo '{"agent_may_write": ["tech-doc"], "human_only": ["user-facing-copy"]}' > $T/auth.json
python3 $S/authorship-boundary-20261107.py --config $T/auth.json --kind user-facing-copy > $T/o41.txt 2>&1; ck "authorship human-only no approval -> rc2" 2 $?
python3 $S/authorship-boundary-20261107.py --config $T/auth.json --kind user-facing-copy --approved > $T/o42.txt 2>&1; ck "authorship human-only approved -> PASS" 0 $?
python3 $S/authorship-boundary-20261107.py --config $T/auth.json --kind tech-doc > $T/o43.txt 2>&1; ck "authorship agent-may -> PASS" 0 $?

echo "== 11. code-review-gate =="
mkdir -p $T/review
python3 $S/code-review-gate-20261107.py t1 --review-dir $T/review --legacy-dir $T/empty-legacy > $T/o44.txt 2>&1; ck "review no record -> BLOCKED rc1" 1 $?
echo '{"verdict": "PASS"}' > $T/review/review-t1-standards.json
echo '{"verdict": "PASS"}' > $T/review/review-t1-spec.json
python3 $S/code-review-gate-20261107.py t1 --review-dir $T/review --legacy-dir $T/empty-legacy > $T/o45.txt 2>&1; ck "review both PASS -> PASS" 0 $?
echo '{"verdict": "FAIL", "reasons": ["spec drift"]}' > $T/review/review-t1-spec.json
python3 $S/code-review-gate-20261107.py t1 --review-dir $T/review --legacy-dir $T/empty-legacy > $T/o46.txt 2>&1; ck "review spec FAIL -> BLOCKED rc1" 1 $?

echo "== 12. live verifier probe (0 VERIFYING expected) =="
python3 $S/c2-verifier-20261104.py > $T/o47.txt 2>&1; ck "verifier probe exit 0" 0 $?
ck_contains "verifier no-op probe" $T/o47.txt "no verifying task"

echo "== RESULT: FAIL=$FAIL =="
exit $FAIL
