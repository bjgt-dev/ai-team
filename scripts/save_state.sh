#!/bin/bash
# save_state.sh - 会话结束前回写状态（四参数强制）
# 用法：save_state.sh "context摘要" "pending更新" "handoff更新" "本次摘要(quota)"
# 缺任一参数：打印用法、退出码 1、不写任何文件。
set -u
STATE_DIR="/home/ubuntu/ai-team/state"
if [ $# -ne 4 ] || [ -z "${1:-}" ] || [ -z "${2:-}" ] || [ -z "${3:-}" ] || [ -z "${4:-}" ]; then
  echo "用法：save_state.sh \"context摘要\" \"pending更新\" \"handoff更新\" \"本次摘要\"" >&2
  echo "错误：四个参数必须齐全且非空，本次未写入任何文件。" >&2
  exit 1
fi
printf "%s\n" "$1" > "$STATE_DIR/context.md"
printf "%s\n" "$2" > "$STATE_DIR/pending.md"
printf "%s\n" "$3" > "$STATE_DIR/handoff.md"
printf "%s | 账号=%s | 本次=%s | 消耗=%s | 剩余=%s\n" "$(date "+%Y-%m-%d %H:%M %Z")" "${MUSE_ACCOUNT:-未标注}" "$4" "平台侧计量（未计量）" "未知" >> "$STATE_DIR/quota.md"
echo "state 已回写。quota.md 末行："
tail -n 1 "$STATE_DIR/quota.md"
