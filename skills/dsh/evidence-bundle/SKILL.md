---
name: evidence-bundle
description: 生成合法证据包，parts 合并到 final，manifest 和 receipts 规则
---
# Evidence Bundle

1. 子 agent 写入 parts/<executor>/，你负责合并到 final/。
2. manifest.json 必须包含 final/ 下每个文件的 sha256。
3. receipts.jsonl 每条必须带 executor_name 和 sub_task_id。
4. acceptance_self_check.json 必须逐条对照 ACCEPTANCE_CRITERIA，
   标 PASS/FAIL/UNVERIFIED，附证据路径。
5. 不向 /output/<task_id>/ 以外写入。
