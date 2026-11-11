#!/bin/bash
# implementation-gate.sh -- 硬门禁（thin client，切运 2026-11-10，S6）
# 用法：implementation-gate.sh --task <slug> [--require-memory] [--require-kb] [--require-plan]
# 检查 gate-svc 数据库里的回执/批准（24h 内有效），不再读本地 .done 文件：
#   memory  receipts kind=memory（hubctl search 产生）
#   kb      receipts kind=kb（hubctl search 产生）
#   plan    plan_approvals（董事长经 gate-svc /approve 生成，hubctl approve）
# 缺任一必需项即 exit 1，并记入台账；全过才 exit 0。
# 证据由 hubctl 产生，agent 不写证据；实施动作走 hubctl act；绕过无权限。
# 这是可读提示；真正强制在枢纽服务端（gate-svc + DB 触发器）。
set -u
TASK=""; REQ_MEM=0; REQ_KB=0; REQ_PLAN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --task) TASK="$2"; shift 2 ;;
    --require-memory) REQ_MEM=1; shift ;;
    --require-kb) REQ_KB=1; shift ;;
    --require-plan) REQ_PLAN=1; shift ;;
    *) echo "FAIL：未知参数 $1"; exit 1 ;;
  esac
done
[ -n "$TASK" ] || { echo "FAIL：须给 --task <slug>"; exit 1; }
LEDGER="$HOME/workspace/tools/implementation-gate-ledger.jsonl"
mkdir -p "$(dirname "$LEDGER")"
check_receipt() {
  local kind="$1"
  local n
  n=$(ssh arm-agent "docker exec ops-pg psql -U postgres -d central_queue -A -t -c \"SELECT COUNT(*) FROM receipts WHERE slug='${TASK}' AND kind='${kind}' AND ts > now() - interval '24 hours';\"" 2>/dev/null | tr -d '[:space:]')
  [ -n "$n" ] && [ "$n" -gt 0 ]
}
check_plan() {
  local n
  n=$(ssh arm-agent "docker exec ops-pg psql -U postgres -d central_queue -A -t -c \"SELECT COUNT(*) FROM plan_approvals WHERE task_slug='${TASK}' AND ts > now() - interval '24 hours';\"" 2>/dev/null | tr -d '[:space:]')
  [ -n "$n" ] && [ "$n" -gt 0 ]
}
fail=0
if [ "$REQ_MEM" = 1 ] && ! check_receipt memory; then
  echo "FAIL：memory 未做（gate-svc 无 memory 回执），先跑 hubctl search <seat> $TASK <问题>"
  fail=1
fi
if [ "$REQ_KB" = 1 ] && ! check_receipt kb; then
  echo "FAIL：kb 未做（gate-svc 无 kb 回执），先跑 hubctl search <seat> $TASK <问题>"
  fail=1
fi
if [ "$REQ_PLAN" = 1 ] && ! check_plan; then
  echo "FAIL：plan 未批准（gate-svc 无 plan_approvals），须董事长经 hubctl approve 批准"
  fail=1
fi
if [ "$fail" = 1 ]; then
  printf '{"ts":"%s","task":"%s","result":"FAIL"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$TASK" >> "$LEDGER"
  exit 1
fi
printf '{"ts":"%s","task":"%s","result":"PASS"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$TASK" >> "$LEDGER"
echo "GATE PASS：gate-svc 回执/批准齐全，可以实施（实施动作走 hubctl act）"
exit 0
