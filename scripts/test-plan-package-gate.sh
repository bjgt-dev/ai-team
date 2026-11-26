#!/bin/bash
# test-plan-package-gate.sh — 方案包门禁测试（2026-11-10 董事长令；2026-11-26 单顾问豁免更新）
# 六个用例：
#   bad         缺专家评审（2026-11-10 本席真实犯错：动态角色设计没过专家就推给董事长）→ 必须 FAIL
#   good        五件齐 + 双顾问确认 → 必须 PASS
#   one-expert  专家评审只一家、无豁免声明 → 必须 FAIL
#   no-confirm  两家专家但无确认标记 → 必须 FAIL
#   single-advisor        一家专家（ChatGPT）+ 单顾问推进声明 + 待补项记录 → 必须 PASS（单顾问豁免）
#   single-advisor-no-backlog  一家专家 + 豁免声明、但无待补项记录 → 必须 FAIL
set -u
GATE="$(cd "$(dirname "$0")" && pwd)/plan-package-gate.py"
BASE="/tmp/plan-gate-test"
rm -rf "$BASE"

mkbase() {
  mkdir -p "$1"
  printf '# 背景\n团队要 24 小时闭环。\n' > "$1/background.md"
  printf '# 需求\n角色动态扩展、按需启停。\n' > "$1/requirements.md"
  printf '# 业内最佳实践\n角色 = 文件夹包 + 注册表 + 热加载。\n' > "$1/best-practices.md"
  printf '# 方案\nroles/<slug>/ + 注册表 roles 节。\n' > "$1/design.md"
}

mkbase "$BASE/bad"

mkbase "$BASE/good"
cat > "$BASE/good/expert-review.md" <<'EOF'
# 专家评审记录
- ChatGPT（发散）：建议补角色门禁脚本；采纳，已加入设计第 2 步。
- Claude（批判）：指出 KB 索引无自动重建是翻车点；采纳，先修通 worker-1。
- 结论：双顾问确认，修订后定稿。
EOF

mkbase "$BASE/one-expert"
cat > "$BASE/one-expert/expert-review.md" <<'EOF'
# 专家评审记录
- DeepSeek：方案可行；采纳。
- 结论：双顾问确认。
EOF

mkbase "$BASE/no-confirm"
cat > "$BASE/no-confirm/expert-review.md" <<'EOF'
# 专家评审记录
- ChatGPT：建议补门禁；采纳。
- Claude：指出索引缺口；采纳。
EOF

mkbase "$BASE/single-advisor"
cat > "$BASE/single-advisor/expert-review.md" <<'EOF'
# 专家评审记录
- ChatGPT（发散）：建议补角色门禁脚本；采纳，已加入设计第 2 步。
- 结论：专家确认。
- 单顾问推进声明：批判通道因故障跑不成，按董事长 2026-11-25 单顾问推进令，以单顾问（ChatGPT）确认推进；缺失的批判评审记为待补项，通道恢复后补做。
EOF

mkbase "$BASE/single-advisor-no-backlog"
cat > "$BASE/single-advisor-no-backlog/expert-review.md" <<'EOF'
# 专家评审记录
- ChatGPT（发散）：建议补角色门禁脚本；采纳，已加入设计第 2 步。
- 结论：专家确认。
- 单顾问推进声明：批判通道因故障跑不成，按董事长 2026-11-25 单顾问推进令，以单顾问（ChatGPT）确认推进；缺失的批判评审后续再补。
EOF

FAIL=0
run_case() {
  local name="$1" expect="$2"
  python3 "$GATE" "$BASE/$name" > "/tmp/plan-gate-test-$name.out" 2>&1
  local got=$?
  if [ "$got" -eq "$expect" ]; then
    echo "PASS case=$name expect=$expect got=$got"
  else
    echo "FAIL case=$name expect=$expect got=$got"
    cat "/tmp/plan-gate-test-$name.out"
    FAIL=1
  fi
}

run_case bad 1
run_case good 0
run_case one-expert 1
run_case no-confirm 1
run_case single-advisor 0
run_case single-advisor-no-backlog 1

if [ "$FAIL" -eq 0 ]; then echo "ALL 6 CASES PASS"; else echo "SOME CASES FAILED"; fi
exit $FAIL
