#!/bin/bash
# answer-gate.sh -- 答前硬门禁（thin client，切运 2026-11-10，S6）
# 用法：answer-gate.sh --question <slug>
# 检查两项（缺一即 exit 1）：
#   ① seat-wake-gate 通过（4 小时内跑过唤醒，本地标记是新的）
#   ② gate-svc 数据库里有该 slug 的 memory 与 kb 回执（24h 内有效）
#      —— 不再读本地 .done 文件（gate-record.sh 写证据已禁用，可伪造）。
#      回执只能由 gate-svc /search 产生（hubctl search）。
# 每次结果记入 ~/workspace/tools/answer-gate-ledger.jsonl。
# 这是可读提示；真正强制在枢纽服务端（gate-svc + DB 触发器）。
set -u
SLUG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --question) SLUG="$2"; shift 2 ;;
    *) echo "FAIL：未知参数 $1"; exit 1 ;;
  esac
done
[ -n "$SLUG" ] || { echo "FAIL：须给 --question <slug>"; exit 1; }
LEDGER="$HOME/workspace/tools/answer-gate-ledger.jsonl"
mkdir -p "$(dirname "$LEDGER")"
# ① 唤醒门禁
if ! bash "$HOME/workspace/tools/seat-wake-gate.sh" > /dev/null 2>&1; then
  printf '{"ts":"%s","q":"%s","result":"FAIL","reason":"wake-gate"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SLUG" >> "$LEDGER"
  echo "GATE FAIL：唤醒过期或没跑，先跑 bash ~/workspace/tools/wake-up-check.sh"
  exit 1
fi
# ② gate-svc 回执（只读查库，不写证据）
check_receipt() {
  local kind="$1"
  local n
  n=$(ssh arm-agent "docker exec ops-pg psql -U postgres -d central_queue -A -t -c \"SELECT COUNT(*) FROM receipts WHERE slug='${SLUG}' AND kind='${kind}' AND ts > now() - interval '24 hours';\"" 2>/dev/null | tr -d '[:space:]')
  [ -n "$n" ] && [ "$n" -gt 0 ]
}
fail=""
for kind in memory kb; do
  if ! check_receipt "$kind"; then
    fail="$fail $kind 未查"
  fi
done
if [ -n "$fail" ]; then
  printf '{"ts":"%s","q":"%s","result":"FAIL","reason":"%s"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SLUG" "$fail" >> "$LEDGER"
  echo "GATE FAIL：$fail；先跑 hubctl search <seat> $SLUG <问题> 取回执再答"
  exit 1
fi
printf '{"ts":"%s","q":"%s","result":"PASS"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SLUG" >> "$LEDGER"
echo "GATE PASS：唤醒新鲜、记忆与知识库回执（gate-svc）都有效，可以答"
exit 0
