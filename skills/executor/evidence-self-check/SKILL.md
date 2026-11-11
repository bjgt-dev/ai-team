---
name: evidence-self-check
description: 执行前读验收条件，每步写自检 JSON，无法完成标记 FAILED
---
# Evidence Self-Check

1. 开始前从 TASK_CONTRACT 读取 ACCEPTANCE_CRITERIA。
2. 每完成一个可验收步骤，写入 acceptance_self_check.json：
   - criterion / result(PASS|FAIL|UNVERIFIED) / evidence
3. 全部完成后生成 manifest.json（每个文件 sha256）。
4. 无法完成时如实标记 FAIL，不用假文件/假日志/假数据填充。
