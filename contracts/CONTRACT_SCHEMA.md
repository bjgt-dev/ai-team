# 契约 Schema v1.1（2026-10-20 董事长/监理定版）

机器可读定义：`contracts/contract.schema.json`；校验器：`scripts/validate_contract.py`（无依赖，退出码 0/1）。自 2026-10-20 起所有新契约必须过校验才许入队；存量契约缺新字段的列为 legacy，补投时按 v1.1 补齐。

## 必填字段

| 字段 | 含义 |
|---|---|
| task_id | 全局唯一 ID（小写字母/数字/-/_） |
| task_type | llm_generate / llm_extract_events |
| objective | 输入：任务目标与素材引用 |
| output_file | 输出格式：证据包内产物相对路径（如 artifacts/out.md） |
| acceptance_criteria | 验收规则数组：{type: file_exists/file_min_size, target, expected?}，verify.py 逐条裁决；review 长度口径以 file_min_size 为准 |
| timeout_s | 超时时间（秒，30–1800）：单次执行硬上限，reaper 租约以此为准 |
| max_retries | 重试上限（0–5）：卡死重投与门禁打回共享此预算 |
| idempotency_key | 幂等键：同一逻辑任务重投/重派复用同一 key，执行器凭此去重；缺省 = task_id |
| evidence_required | 证据包必含文件（固定含 receipts.jsonl、manifest.json、acceptance_self_check.json） |
| created_at | ISO 8601 带时区；回执不得早于此时间（容差 120s） |
| source | 契约来源（派单方标识） |

## 可选字段

model（缺省 executor-primary）、max_tokens、executor_preference（any/guangtou/noesis）、input_files、lease_s（缺省 = timeout_s）、attempts（调度层维护的已消耗重试数）、preferred_worker（门禁打回时写入原工人，认领端过滤）。

## 校验

`python3 scripts/validate_contract.py <contract.json>` — PASS 打印 task_id/幂等键/超时/重试；FAIL 逐条列出缺失或违规字段。
