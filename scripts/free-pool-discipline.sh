#!/usr/bin/env bash
# VPS 强制免费池纪律（2026-10-20 董事长令）
# 所有经 SSH 在 VPS 上跑的自动化脚本，必须 source 此文件

# 1. 强制 newapi 网关，严禁直连上游
export OPENAI_BASE_URL="http://127.0.0.1:3001/v1"
export ANTHROPIC_BASE_URL="http://127.0.0.1:3001/v1"
# 清掉可能存在的直连 key（防烧钱）
unset ANTHROPIC_API_KEY
unset OPENAI_API_KEY

# 2. 模型强制免费池
export DEFAULT_MODEL="executor-primary"
export REVIEW_MODEL="verifier-stable"
export SCHEDULER_MODEL="scheduler-stable"

# 3. 重试上限：最多 2 次，第 3 次直接停
export MAX_RETRIES=2

# 4. 熔断标记文件：连续失败时写入，脚本检查到即停
export CIRCUIT_BREAKER="/tmp/vps-circuit-breaker.tripped"

# 检查熔断
if [ -f "$CIRCUIT_BREAKER" ]; then
  echo "CIRCUIT BREAKER TRIPPED, aborting" >&2
  exit 1
fi
