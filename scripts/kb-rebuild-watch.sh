#!/bin/bash
# kb-rebuild-watch.sh — 知识库自动重建（2026-11-10 董事长令：知识库要让团队用起来，索引不能过期）
# 机制：每 30 分钟由 cron 跑一次；若 knowledge/ 有文件比 index.npz 新，且没有重建在跑，
# 则后台启动 build_index.py 重建索引。重建约 21–31 分钟，跑完自动写新 index.npz/index.json。
# 2026-11-25 第二波补两道保险（BACKLOG §3）：
#   1. 失败告警：每轮先结算上一轮构建——日志含 "[index] wrote index.npz + index.json"
#      或 index.npz mtime 前进过构建开始时间 → 成功；含 Traceback / 无日志 / 无成功
#      信号 → 失败，写 state/outbox/kb-rebuild-failed-<ts>.txt（同一构建只报一次）。
#   2. 停滞告警：索引 >48 小时未更新、需要重建、无构建在跑、且上次构建开始于 3 小时
#      前（或从未开始）→ 写 state/outbox/kb-rebuild-stale-<ts>.txt，6 小时节流；
#      告警后仍会正常启动构建（告警不挡修复）。
# 状态文件：$HOME/ai-team/state/kb-rebuild-watch-state.json
# 用法：kb-rebuild-watch.sh [--dry-run]
set -u
KB_DIR="$HOME/ai-team/state/kb-retrieval-20261106"
KNOW_DIR="$HOME/ai-team/knowledge"
INDEX="$KB_DIR/index.npz"
LOG_DIR="/tmp"
STATE_FILE="$HOME/ai-team/state/kb-rebuild-watch-state.json"
OUTBOX="$HOME/ai-team/state/outbox"
STALE_HOURS=48
STALE_ALERT_THROTTLE_SEC=21600
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

outbox_alert() {
  # outbox_alert <kind: failed|stale> <detail...>
  local kind="$1"; shift
  local ts
  ts=$(date +%Y%m%d-%H%M%S)
  local title="知识库索引重建失败告警"
  [ "$kind" = "stale" ] && title="知识库索引停滞告警"
  local fname="kb-rebuild-$kind-$ts.txt"
  mkdir -p "$OUTBOX"
  {
    echo "$title"
    echo "- 时间：$(date '+%Y-%m-%d %H:%M CST')"
    echo "- 索引：$INDEX"
    echo "- 详情：$*"
  } > "$OUTBOX/$fname"
  echo "alert written: outbox/$fname"
}

state_get() {
  python3 -c "import json,sys; d=json.load(open('$STATE_FILE')); print(d.get('$1',''))" 2>/dev/null || echo ""
}

state_set() {
  # state_set <key> <value> — 用 python 合并写回（无 jq 依赖）
  python3 - "$1" "$2" <<'PYEOF'
import json, os, sys
path = os.environ["STATE_FILE"]
try:
    d = json.load(open(path))
except (OSError, ValueError):
    d = {}
d[sys.argv[1]] = sys.argv[2]
json.dump(d, open(path, "w"), ensure_ascii=False, indent=2)
PYEOF
}

# 0. 已有重建在跑就不重复启动（也不结算——构建还没结束）
if pgrep -f "build_index.py" >/dev/null 2>&1; then
  echo "rebuild already running, skip"
  exit 0
fi

# 1. 结算上一轮构建（只在非 dry-run 时写状态与告警）
LAST_STARTED=$(state_get last_started_epoch)
LAST_LOG=$(state_get last_log)
LAST_OUTCOME=$(state_get last_outcome)
LAST_OUTCOME_FOR=$(state_get last_outcome_for)
if [ "$DRY_RUN" = "0" ] && [ -n "$LAST_STARTED" ] && [ "$LAST_OUTCOME_FOR" != "$LAST_STARTED" ]; then
  IDX_T=$(stat -c %Y "$INDEX" 2>/dev/null || echo 0)
  OUTCOME="failed"
  REASON="构建结束但无成功信号"
  if [ -n "$LAST_LOG" ] && [ -f "$LAST_LOG" ]; then
    if grep -q "wrote index.npz + index.json" "$LAST_LOG" 2>/dev/null; then
      OUTCOME="success"; REASON="日志含 wrote index.npz + index.json"
    elif grep -qi "Traceback" "$LAST_LOG" 2>/dev/null; then
      OUTCOME="failed"; REASON="日志含 Traceback"
    elif [ "$IDX_T" -gt "$LAST_STARTED" ]; then
      OUTCOME="success"; REASON="index.npz mtime 已前进过构建开始时间"
    fi
  else
    REASON="构建日志缺失（$LAST_LOG）"
  fi
  export STATE_FILE
  state_set last_outcome "$OUTCOME"
  state_set last_outcome_for "$LAST_STARTED"
  state_set last_outcome_ts "$(date +%s)"
  if [ "$OUTCOME" = "failed" ]; then
    outbox_alert failed "上一轮构建失败：开始时间 $(date -d "@$LAST_STARTED" '+%Y-%m-%d %H:%M' 2>/dev/null || echo "$LAST_STARTED")，日志 $LAST_LOG，原因：$REASON。"
  else
    echo "previous rebuild: success ($REASON)"
  fi
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

# 3. 停滞告警：索引 >48h 未更新、需要重建、无构建在跑、上次构建开始于 3 小时前（或从未开始）
if [ "$DRY_RUN" = "0" ] && [ "$NEED" = "1" ] && [ -f "$INDEX" ]; then
  NOW=$(date +%s)
  IDX_T=$(stat -c %Y "$INDEX" 2>/dev/null || echo 0)
  AGE_H=$(( (NOW - IDX_T) / 3600 ))
  if [ "$AGE_H" -ge "$STALE_HOURS" ]; then
    LAST_START_EPOCH=$(state_get last_started_epoch)
    STALE_OK=0
    if [ -z "$LAST_START_EPOCH" ]; then
      STALE_OK=1
    elif [ $(( NOW - LAST_START_EPOCH )) -ge 10800 ]; then
      STALE_OK=1
    fi
    if [ "$STALE_OK" = "1" ]; then
      LAST_ALERT=$(state_get last_stale_alert_ts)
      if [ -z "$LAST_ALERT" ] || [ $(( NOW - LAST_ALERT )) -ge "$STALE_ALERT_THROTTLE_SEC" ]; then
        outbox_alert stale "索引已 $AGE_H 小时未更新（超过 ${STALE_HOURS} 小时阈值），knowledge/ 有新文件待索引；本轮将启动重建。"
        export STATE_FILE
        state_set last_stale_alert_ts "$NOW"
      fi
    fi
  fi
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
export STATE_FILE
state_set last_started_epoch "$(date +%s)"
state_set last_log "$LOG"
state_set last_outcome ""
state_set last_outcome_for ""
echo "rebuild started, log=$LOG"
