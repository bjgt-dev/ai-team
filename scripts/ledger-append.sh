#!/bin/bash
# ledger-append.sh — 执行台账唯一写入口（2026-11-10 覆盖事故后立）。
# 规则：台账类文件只追加，禁整文件覆盖，写前先备份。
# 用法（在枢纽上跑）：ledger-append.sh <entry-file.md>
#   1. 先把现台账备份为 execution-ledger.md.bak-YYYYMMDD-HHMM
#   2. 再把 entry 文件内容追加到台账末尾
# 绝不允许 scp/cp 整文件覆盖台账。
set -euo pipefail
LEDGER="${LEDGER:-/home/ubuntu/ai-team/state/execution-ledger.md}"
ENTRY="${1:?用法：ledger-append.sh <entry-file.md>}"
[ -f "$ENTRY" ] || { echo "ERROR: entry 文件不存在：$ENTRY" >&2; exit 2; }
[ -f "$LEDGER" ] || { echo "ERROR: 台账不存在：$LEDGER" >&2; exit 2; }
STAMP="$(date +%Y%m%d-%H%M)"
cp "$LEDGER" "${LEDGER}.bak-${STAMP}"
cat "$ENTRY" >> "$LEDGER"
echo "OK: 已备份 ${LEDGER}.bak-${STAMP}，已追加 $(wc -l < "$ENTRY") 行"
