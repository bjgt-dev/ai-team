---
name: external-resources-first
description: 【前置必读】外部资源优先工作流：遇未知卡点先全网搜索（GitHub Issues/Reddit/官方文档），再专家会诊（外脑 Gemini + newapi gpt-oss-120b），至少 2 个思路对比后才许动破坏性代码，解法沉淀成 Skill。接管与接单前必须加载，未读不许接单。
---

# 外部资源优先工作流（前置 Skill，全队强制）

中枢 canonical 总纲：`docs/AGENTS.md`。本 Skill 是它的可执行版，任何账号接管或接单前先加载本 Skill。

## 四步（顺序不可颠倒）

1. **搜索优先**：遇报错 / 未知难题，第一动作是全网搜——GitHub Issues 与开源项目、Reddit 踩坑贴、官方文档、YouTube 教程。提炼别人已验证的解法，不许先抛自己的猜测。
2. **专家会诊**：自己搞不定或连错 2 次 → 结构化求助外脑 Gemini（`scripts/web_brain.py`，四件套：背景 + 卡点 + 已试方案 + 要求 A/B 及优缺点）；或调 newapi 现有最强模型 gpt-oss-120b / foreman-prime。**expert-claude / expert-gpt 别名不存在，禁止编造调用**；新专家渠道须董事长拍板。
3. **拒绝盲目试错**：破坏性 / 不可逆动作前，必须有 ≥2 个可行思路并写明优缺点对比，二选一才动工；可逆小实验先说清退路。
4. **沉淀成 Skill**：通用解法当场同化进技能库（三关：环境隔离 / 防投毒 / 降级兜底），更新注册表与 route.py 规则。

## 避坑警告

- 只写进某个账号的本地笔记 = 没立规矩。规则必须落中枢（docs/ 或 skills/），boot.sh 强制输出总纲就是为此。
- 搜索结论要标注来源与日期；搜索摘要里的相对时间不可信，关键事实开原文核对。
