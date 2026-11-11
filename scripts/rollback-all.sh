#!/bin/bash
# rollback-all.sh — 切回旧模式（R2）。触发条件（任一即自动执行并报董事长）：
#  合法任务被误拦且 15 分钟内无法修复 / gate-svc 不可用超 5 分钟 /
#  central_queue 派单中断 / 检索命中率较切换前基线下降超 30%。
# 步骤：gate.mode=off；执行所有 down 迁移；把凭证还给原账户（break-glass）；
# 启回旧向量服务；恢复旧脚本。耗时目标 ≤10 分钟。幂等：重复跑安全。
set -u
AI_TEAM=/home/ubuntu/ai-team
LOG=$AI_TEAM/state/cutover-20261110.log
START=$(date +%s)
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ROLLBACK-START" | tee -a "$LOG"

# 1) 总开关 off
sudo mkdir -p /etc/noesis
echo off | sudo tee /etc/noesis/gate.mode > /dev/null
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ROLLBACK gate.mode=off" | tee -a "$LOG"

# 2) 停 gate-svc
systemctl --user stop gate-svc 2>/dev/null || true
pkill -f "gate-svc" 2>/dev/null || true
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ROLLBACK gate-svc stopped" | tee -a "$LOG"

# 3) 执行 down 迁移（倒序 005→004→003→002→001；IF EXISTS，幂等；cutover51 增补 004/005）
for m in 005_watchdog_registry_down 004_cutover51_down 003_seat_roles_down 002_central_triggers_down 001_gate_tables_down; do
  if [ -f "$AI_TEAM/state/migrations/$m.sql" ]; then
    docker exec -i ops-pg psql -U postgres -d central_queue -v ON_ERROR_STOP=1 \
      < "$AI_TEAM/state/migrations/$m.sql" >> "$LOG" 2>&1 \
      && echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ROLLBACK down $m OK" | tee -a "$LOG" \
      || echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ROLLBACK down $m FAIL (see log)" | tee -a "$LOG"
  else
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ROLLBACK down $m SKIP (no file)" | tee -a "$LOG"
  fi
done

# 4) 把凭证还给原账户（来自 break-glass 封存；只恢复文件，不打印值）
if [ -d /etc/noesis/break-glass ]; then
  "$AI_TEAM/scripts/break-glass-restore.sh" >> "$LOG" 2>&1 \
    && echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ROLLBACK credentials restored from break-glass" | tee -a "$LOG" \
    || echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ROLLBACK credentials restore FAIL" | tee -a "$LOG"
else
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ROLLBACK break-glass missing, credentials NOT restored" | tee -a "$LOG"
fi

# 5) 启回旧向量服务（kb-service，若存在；用其 venv，系统 python 无 FlagEmbedding）
#    模型加载需 1-2 分钟，轮询最长 180 秒；用 setsid + < /dev/null 彻底脱离 SSH 会话防挂住。
if [ -f "$AI_TEAM/state/kb-retrieval-20261106/kb-service.py" ]; then
  KB_PY="$AI_TEAM/state/kb-retrieval-20261106/venv/bin/python"
  [ -x "$KB_PY" ] || KB_PY="python3"
  (setsid nohup "$KB_PY" kb-service.py > /tmp/kb-service-restored.log 2>&1 < /dev/null &) || true
  disown 2>/dev/null || true
  UP=0
  for _ in $(seq 1 36); do
    sleep 5
    if curl -s -m 5 http://127.0.0.1:8092/health > /dev/null 2>&1; then UP=1; break; fi
  done
  if [ "$UP" = 1 ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ROLLBACK vector service back (8092 up)" | tee -a "$LOG"
  else
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ROLLBACK vector service NOT up (check /tmp/kb-service-restored.log)" | tee -a "$LOG"
  fi
else
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ROLLBACK no kb-service.py, vector service SKIP" | tee -a "$LOG"
fi

# 6) 恢复旧脚本（gate-record.sh 写证据功能；旧门禁脚本）
for f in gate-record.sh seat-wake-gate.sh answer-gate.sh implementation-gate.sh kb-ask.sh; do
  bak=$(ls -t "$AI_TEAM/scripts/$f.bak-cutover-20261110" 2>/dev/null | head -1)
  if [ -n "$bak" ] && [ -f "$bak" ]; then
    cp "$bak" "$AI_TEAM/scripts/$f"
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ROLLBACK restored $f from backup" | tee -a "$LOG"
  fi
done

END=$(date +%s)
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ROLLBACK-DONE elapsed=$((END-START))s" | tee -a "$LOG"
