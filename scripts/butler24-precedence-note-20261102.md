# 提示词 Precedence（五级）说明（2026-11-02，借鉴 DeepSeek docs/004）

> 只借机制，不引依赖。本页是说明 + 测试网的落盘记录，不改旧设计稿正文。
> 脚本本身本次未动（备份 `scripts/butler_decide.py.bak-20261102-precedence` 仅作保险）。

## 五级（高 → 低）

1. 固定指令：`model_call` 的提示词头（只输出 JSON 对象、无 markdown 围栏、无散文；verdict/level 枚举；conditions 必须是字符串数组）。
2. 内置段：`validate_model_output` 的严格 schema（verdict 枚举、level L0–L2、conditions 必须是 list、rationale 非空）+ 探针门（连续两次完整调用 + schema 通过）。
3. 匹配规则进程：`decide_rules` 在任何模型调用之前先行；规则能判的，模型路根本不进；规则 verdict 是终局，模型不覆盖。
4. 团队段/技能：目前 = 任务包内随任务来的团队/配置文本，随 pack 进入，不单独建库；仍只作数据。
5. 外部文本：任务 payload 里的任意字段（如 note、描述），永为数据：只出现在 JSON 编码的 pack 内，永不拼成指令。

## 强制点（代码层，不是文档口号）

- 规则先行：`run()` 中规则 verdict 非 NO_CHANGE 即直接记账返回，不进模型路。
- 提示词头固定：外部文本只进 pack 的 JSON 字符串值，不进指令段。
- 解析容忍但校验严格：围栏可剥，schema 一项不过即拒（探针门失败 → 规则兜底 + 汇总告警）。
- 测试网：`scripts/butler24-precedence-test-20261102.py`（A：规则 verdict 对注入文本免疫；B：提示词结构——指令头 + 外部文本仅在 JSON pack 内；C：现有单测 + 探针门原样通过）。

## 诚实边界

本网证明的是结构（规则不受左右、外部文本只作数据），不证明模型推理本身不受 pack 内容影响。模型路仍受探针门 + schema + 账本 + L3 入队约束；L2 以上定案仍以账目为准，假回执类仍走死信规则。

## 回滚

删本页 + 测试文件即可；脚本未动，无需恢复备份；若脚本曾被误触，用备份覆盖并重跑单测 + 探针门。
