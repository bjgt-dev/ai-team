#!/bin/bash
# 巡航入口（2026-10-24 重构，2026-10-25 14:07 的任务锁改动已回滚：tasks 目录常年非空，按“非空即抢占”会让巡航永远不跑）
python3 /home/ubuntu/ai-team/scripts/patrol_collect.py >> /home/ubuntu/ai-team/logs/ceo-seat.log 2>&1
exec /home/ubuntu/ai-team/scripts/patrol_run.sh
