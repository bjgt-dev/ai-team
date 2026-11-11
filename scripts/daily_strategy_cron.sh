#!/bin/bash
# daily_strategy_cron.sh - 合伙人战略备包（早 07:00 / 晚 20:30）
# 把 KPI 汇报需要的弹药备齐到 state/strategy_latest.md：系统健康、队列、告警、待选库、最新情报指针
MODE="${1:-am}"
OUT=/home/ubuntu/ai-team/state/strategy_latest.md
{
  echo "# 战略备包 · $(date '+%F %T') · $MODE"
  echo
  echo "## 系统健康"
  echo "- 磁盘: $(df / | awk 'NR==2{print $5}') 已用"
  echo "- 网关: $(docker ps --format '{{.Names}} {{.Status}}' | grep -i gateway || echo 'not found')"
  echo "- 待认领契约: $(ls /home/ubuntu/ai-team/contracts/pending/muse/ 2>/dev/null | wc -l) 个"
  echo "- 今日门禁裁决: $(grep -l "$(date +%F)" /home/ubuntu/ai-team/logs/verify-*.json 2>/dev/null | wc -l) 份"
  echo
  echo "## 告警（alerts.log 末 5 行）"
  tail -5 /home/ubuntu/ai-team/state/alerts.log 2>/dev/null || echo "- 无"
  echo
  echo "## 待拍板（pending.md 摘要）"
  head -30 /home/ubuntu/ai-team/state/pending.md 2>/dev/null
  echo
  echo "## 技能待选库（末 15 行）"
  tail -15 /home/ubuntu/ai-team/skills-registry/candidates.md 2>/dev/null
  echo
  echo "## 最新情报简报"
  ls -la /home/ubuntu/ai-team/state/intel_latest.md 2>/dev/null || echo "- 暂无"
} > "$OUT.tmp" && mv "$OUT.tmp" "$OUT"
echo "strategy pack ($MODE) written: $(wc -c < $OUT) bytes"
