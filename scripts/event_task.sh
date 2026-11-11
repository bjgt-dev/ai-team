#!/bin/bash
# 单任务定向执行（2026-10-25 事件驱动第二版）：唤醒只做 tasks 目录最新单，不跑全量巡航。
# 用法：event_task.sh [task-file]
# 若无参数，取 ceo-seat/tasks/ 最新 .md；若文件含 kick-test-rc126，则提示词只做该单的 scripts 读取验证。
set -u
BASE=/home/ubuntu/ai-team
[ -f $BASE/state/brain-proxy.env ] && . $BASE/state/brain-proxy.env
P=${BRAIN_PROXY_PORT:-10814}
export HTTP_PROXY=http://127.0.0.1:$P HTTPS_PROXY=http://127.0.0.1:$P http_proxy=http://127.0.0.1:$P https_proxy=http://127.0.0.1:$P NO_PROXY=127.0.0.1,localhost
cd $BASE
TASK=${1:-$(ls -t $BASE/ceo-seat/tasks/*.md 2>/dev/null | head -1)}
echo "$(date "+%F %T") event-task start task=$TASK" >> $BASE/state/event-task.log
MODEL="my-gateway/gpt-oss-120b"
timeout 600 opencode run --model "$MODEL" "只做这一单，不跑全量巡航，不做其他任务。任务文件：$TASK。读该文件全文并按其要求执行，输出带工单号、状态、四字段或失败码、证据路径的票据到 ceo-seat/out/。若读 scripts 被 external_directory deny，立即回 rc=126 + 错误原文，不得绕行。" </dev/null
RC=$?
echo "$(date "+%F %T") event-task end rc=$RC task=$TASK" >> $BASE/state/event-task.log
