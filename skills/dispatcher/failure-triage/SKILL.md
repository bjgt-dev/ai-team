---
name: failure-triage
description: REJECT 后的重派策略，MAX_RETRIES=2，不通过改 prompt 调教
---
# Failure Triage

1. REJECT 后最多重派 2 次。
2. 重派可以换 executor（Claude Code → OpenCode → Pi），
   但不得修改 acceptance_criteria。
3. 2 次耗尽 → QUARANTINE，标记该 executor 为低可靠度。
4. 某 executor 连续两次 QUARANTINE → 标记 UNRELIABLE，限制接单范围。
5. 禁止通过反复修改 executor 的 prompt 来"调教"它通过门禁。
   废弃比调教更划算。
