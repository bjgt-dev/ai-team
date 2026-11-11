---
name: executor-selection
description: 按 executor_preference 和任务类型选择执行 agent
---
# Executor Selection

- claude → Claude Code（代码/仓库操作优先）
- opencode → OpenCode（多模型兜底、非 Anthropic 生态）
- pi → Pi（需要额外隔离边界，E2B 已启用时）
- auto → 编码/仓库选 Claude Code 或 OpenCode；
  需要额外隔离边界时选 Pi

不要同时让多个 executor 跑同一个子任务"投票"，那是浪费 token。
按任务类型选一个。
