---
name: expert-routing
description: 何时、如何调用专家评审，压缩 briefing 四段结构
---
# Expert Routing

> 本团队实例化说明（2026-10-19）：expert-* 反代渠道未部署（见执行手册附录 A）。
> 本 skill 的路由表保留供未来启用；当前 NEED_HUMAN 直接转人工（董事长）。

1. 仅在以下条件触发专家调用：
   - 契约指定 expert_preference
   - 门禁 NEED_HUMAN 且 unverified_criteria 非空
   - DSH 报告某子任务连续 FAILED 2 次
2. 输入必须是压缩 briefing，四段结构：
   - 任务目标
   - 已尝试的方案
   - 具体卡点
   - 可选项
   禁止转发原始证据包或长日志。
3. 路由规则（待渠道启用）：
   - 默认 expert-gpt
   - 长文档/代码库理解 → expert-gemini
   - 深度推理/架构取舍 → expert-claude
4. 同一任务对同一专家不超过 2 次。
5. 专家返回 NEED_MORE_INFO → 暂停任务，向用户请求补充信息。
6. 专家调用失败 → EXPERT_UNAVAILABLE，不重试。
