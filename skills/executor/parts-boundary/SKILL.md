---
name: parts-boundary
description: 只写 parts/<executor>/，不写 final/，不调用其他 executor
---
# Parts Boundary

1. 只写 /workspace/<task_id>/parts/<provider>/。
2. 不向 final/ 写入。合并由 DSH 负责。
3. 需要其他 executor 时标记 NEED_OTHER_EXECUTOR，
   不要自行调用其他 agent。
