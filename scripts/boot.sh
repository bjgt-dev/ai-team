#!/bin/bash
# boot.sh - TWT 启动恢复脚本（在 arm-agent 上执行）
# 做四件事：测 SSH 专用钥匙实登 / 读 handoff / 读 pending / 读 quota 末行
set -u
STATE_DIR="/home/ubuntu/ai-team/state"
KEY="/home/ubuntu/.ssh-twt2026/id_ed25519"

echo "=== 0. 全队总纲（docs/AGENTS.md，接管必读，未读不许接单）==="
cat "/home/ubuntu/ai-team/docs/AGENTS.md" 2>/dev/null || echo "(总纲缺失！先恢复 docs/AGENTS.md 再继续)"
echo ""
echo "=== 1. SSH 通道检查 ==="
if ssh -i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 ubuntu@localhost "echo ok" 2>/dev/null | grep -q "^ok$"; then
  echo "SSH: OK（专用钥匙 twt2026-ceo 实登通过）"
else
  echo "SSH: FAIL（专用钥匙实登失败，检查 $KEY 与 authorized_keys）"
fi

echo "=== 2. 上次进度（state/handoff.md）==="
cat "$STATE_DIR/handoff.md" 2>/dev/null || echo "(handoff.md 不存在或为空)"

echo "=== 3. 待办清单（state/pending.md）==="
cat "$STATE_DIR/pending.md" 2>/dev/null || echo "(pending.md 不存在或为空)"

echo "=== 4. 额度记录（state/quota.md 末行）==="
tail -n 1 "$STATE_DIR/quota.md" 2>/dev/null || echo "(quota.md 不存在或为空)"

echo "---"
echo "如需切换账号：读 /home/ubuntu/ai-team/state/MUSE-SWITCH.md，按 SOP 执行。"
echo "切换前必须先 save_state.sh 写全四参数，切换后必须 boot.sh 确认四段齐全。"
echo "---"
