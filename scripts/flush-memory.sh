#!/bin/bash
# flush-memory.sh — 压缩前 flush（2026-11-10 董事长令；2026-11-18 彻底修：枢纽本地直写）。
# 用法：flush-memory.sh <slug> <事实内容>
# 写到枢纽 knowledge/收件箱/flush-<slug>-<YYYYMMDD>.md，写完立刻可查。
# 每次 flush 记入 ~/workspace/tools/flush-memory-ledger.jsonl。
# 什么时候跑：对话变长、要换话题、感觉上下文快满、学到重要决定/事实时——不等压缩来丢。
# 2026-11-18 彻底修：本脚本在枢纽上跑，直接写本地 knowledge/收件箱，不走 kb-write.sh 的
# ssh/scp 回环（kb-write.sh 是给本地沙箱向枢纽推文件用的，在枢纽上跑会 ssh 自己失败）。
# 另修假 OK：原 `cmd | tail -1` 取 tail 的 exit 0 恒真，失败也报 OK；现判真实退出码。
set -u
SLUG="$1"; shift
NOTE="$*"
[ -n "$SLUG" ] && [ -n "$NOTE" ] || { echo "FAIL：用法 flush-memory.sh <slug> <事实内容>"; exit 1; }
DATE=$(date +%Y%m%d)
FILE="/tmp/flush-$SLUG-$DATE.md"
DEST="$HOME/ai-team/knowledge/收件箱/flush-$SLUG-$DATE.md"
{
  echo "# flush：$SLUG（$DATE）"
  echo ""
  echo "- 时间：$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "- 事实：$NOTE"
  echo "- 来源：主聊对话，压缩前 flush"
} > "$FILE"
mkdir -p "$(dirname "$DEST")"
if cp "$FILE" "$DEST" 2>/dev/null; then
  SHA_LOCAL=$(sha256sum "$FILE" | awk "{print \$1}")
  SHA_HUB=$(sha256sum "$DEST" | awk "{print \$1}")
  if [ "$SHA_LOCAL" = "$SHA_HUB" ]; then
    mkdir -p "$HOME/workspace/tools"
    printf "{\"ts\":\"%s\",\"slug\":\"%s\",\"file\":\"%s\"}\n" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SLUG" "flush-$SLUG-$DATE.md" >> "$HOME/workspace/tools/flush-memory-ledger.jsonl"
    echo "OK 已写入枢纽 knowledge/收件箱/flush-$SLUG-$DATE.md sha=$SHA_HUB（实时可查）"
    echo "FLUSH OK：已写进枢纽知识库 flush-$SLUG-$DATE.md"
    exit 0
  else
    echo "FLUSH FAIL：写入后 sha 不一致：本地 $SHA_LOCAL 枢纽 $SHA_HUB" >&2
    exit 1
  fi
else
  echo "FLUSH FAIL：cp 写入失败：$DEST" >&2
  exit 1
fi
