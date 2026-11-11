#!/bin/bash
# flush-memory.sh -- 压缩前 flush：把重要事实写进枢纽知识库，防压缩丢掉
# 用法：flush-memory.sh <slug> <事实内容...>
# 写到枢纽 knowledge/ 的 flush-<slug>-<YYYYMMDD>.md（经 kb-write.sh，写完立刻可查）。
# 每次 flush 记入 ~/workspace/tools/flush-memory-ledger.jsonl。
# 什么时候跑：对话变长、要换话题、感觉上下文快满、学到重要决定/事实时——不等压缩来丢。
set -u
SLUG="$1"; shift
NOTE="$*"
[ -n "$SLUG" ] && [ -n "$NOTE" ] || { echo "FAIL：用法 flush-memory.sh <slug> <事实内容>"; exit 1; }
DATE=$(date +%Y%m%d)
FILE="/tmp/flush-$SLUG-$DATE.md"
{
  echo "# flush：$SLUG（$DATE）"
  echo ""
  echo "- 时间：$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "- 事实：$NOTE"
  echo "- 来源：主聊对话，压缩前 flush"
} > "$FILE"
if bash "$HOME/workspace/tools/kb-write.sh" "flush-$SLUG-$DATE.md" "$FILE" 2>&1 | tail -1; then
  mkdir -p "$HOME/workspace/tools"
  printf '{"ts":"%s","slug":"%s","file":"%s"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SLUG" "flush-$SLUG-$DATE.md" >> "$HOME/workspace/tools/flush-memory-ledger.jsonl"
  echo "FLUSH OK：已写进枢纽知识库 flush-$SLUG-$DATE.md"
  exit 0
else
  echo "FLUSH FAIL：kb-write.sh 未成功，事实只在 $FILE，须重跑"
  exit 1
fi
