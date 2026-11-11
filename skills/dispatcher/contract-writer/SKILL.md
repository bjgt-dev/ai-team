---
name: contract-writer
description: 把用户指令拆成可验收的任务契约，至少 2 条代码可判定条件
---
# Contract Writer

1. 判断 execution_profile：
   - 单一、边界清晰 → direct-executor
   - 可拆为调研+产出+测试 → dsh-orchestrated
2. 生成 acceptance_criteria，至少 2 条，类型只能是：
   file_exists / file_min_size / file_contains
3. 禁止使用"报告已提交""已完成"等不可代码判定的描述。
4. 输出契约 JSON 到 $TEAM_ROOT/contracts/<task_id>.json，
   同时写一份 Markdown 版供沙箱读取。
5. 如果用户指令无法拆出至少 2 条可判定条件，返回 NEED_CLARIFICATION，
   不要自行编造验收标准。
