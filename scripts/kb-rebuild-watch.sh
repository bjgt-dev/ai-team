#!/bin/bash
# kb-rebuild-watch.sh — 知识库自动重建（2026-11-10 董事长令：知识库要让团队用起来，索引不能过期）
# 机制：每 30 分钟由 cron 跑一次；若 knowledge/ 有文件比 index.npz 新，且没有重建在跑，
# 则后台启动 build_index.py 重建索引。重建约 21 分钟，跑完自动写新 index.npz/index.json。
# 用法：kb-rebuild-watch.sh [--dry-run]
set -u
KB_DIR="$HOME/ai-team/state/kb-retrieval-20261106"
KNOW_DIR="$HOME/ai-team/knowledge"
INDEX="$KB_DIR/index.npz"
LOG_DIR="/tmp"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

# 1. 已有重建在跑就不重复启动
if pgrep -f "build_index.py" >/dev/null 2>&1; then
  echo "rebuild already running, skip"
  exit 0
fi

# 2. 取 knowledge/ 最新文件时间与索引时间
if [ ! -f "$INDEX" ]; then
  NEED=1
else
  NEWEST=$(find "$KNOW_DIR" -type f \( -name '*.md' -o -name '*.txt' \) -printf '%T@\n' 2>/dev/null | sort -n | tail -1)
  IDX_T=$(stat -c %Y "$INDEX" 2>/dev/null || echo 0)
  # 比较：knowledge 最新文件比索引新（用整数秒比较，留 1 秒容差）
  NEED=$(python3 -c "print(1 if float('$NEWEST') > float('$IDX_T') + 1 else 0)" 2>/dev/null || echo 0)
fi

if [ "$NEED" != "1" ]; then
  echo "index up to date, skip"
  exit 0
fi

TS=$(date +%Y%m%d-%H%M)
LOG="$LOG_DIR/kb-rebuild-auto-$TS.log"
if [ "$DRY_RUN" = "1" ]; then
  echo "DRY-RUN: would rebuild, log=$LOG"
  exit 0
fi
cd "$KB_DIR" && nohup ./venv/bin/python build_index.py > "$LOG" 2>&1 &
echo "rebuild started, log=$LOG"
