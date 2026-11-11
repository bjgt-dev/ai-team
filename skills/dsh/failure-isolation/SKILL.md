---
name: failure-isolation
description: 子 agent 连续失败 2 次即隔离，禁止用自己推理补上
---
# Failure Isolation

1. 某子 agent 连续失败 2 次 → 停止 spawn 它。
2. 把失败写入 orchestration_log.json。
3. 最终报告中标记该子任务 FAILED。
4. 禁止用自己的推理补上失败的子任务。FAILED 就是 FAILED。
