# 记忆与知识库 SOP v1.0（2026-11-10 董事长令，全团队执行）

> 真源：枢纽 `~/ai-team/docs/sops/memory-sop.md`；本地 `~/workspace/sops/memory-sop.md` 只作镜像。
> 机制：`~/ai-team/scripts/memory-gate.py`（门禁脚本，本地镜像 `~/workspace/tools/memory-gate.py`）。
> 原则：写入先过门禁，冲突不靠“最新获胜”；文档不是机制——违反本 SOP 的推进会被 gate 脚本拦截。

## 0. 适用范围

- 本席、光头、Noesis、大管家、总调度、工人、复核席，全部执行。
- 任何记忆写入（MEMORY.md、每日日志、知识库）与知识库改动一律走本 SOP。

## 1. 写入门禁五检查（顺序不可跳）

| 检查 | 内容 | 不过怎么办 |
|---|---|---|
| 1 值得记吗 | 是否为决策、事实、教训、阻塞，不是闲聊与过程 | 不值得记的直接丢弃，不许写入 |
| 2 去重 | 是否已有相同 key 的条目 | 重复的走 CONFIRM（确认）或丢弃，不许重复写 |
| 3 冲突消解 | 与旧条目冲突吗 | 见 §3，不许静默覆盖 |
| 4 贴有效期 | 按分层 TTL 贴过期日 | 无有效期不许写入 |
| 5 脱敏 | 含密码、密钥、token、身份证号等吗 | 含敏感信息不许写入，只记位置指针 |

## 2. 操作类型

- ADD：新增条目。CONFIRM：确认旧条目仍有效。SUPERSEDE：新条目取代旧条目（旧条目标记被取代，保留痕迹）。RETRACT：撤回错误条目。EXPIRE：到期失效。DELETE：删除（须大管家批，只删无用条目）。
- 每个操作须记 proposer（提议人）与 reviewer（审核人），两人不得是同一人。

## 3. 冲突消解（不靠“最新获胜”）

- 先比证据权威性：实测 > 定版原文 > 会议记录 > 口头；证据强的胜。
- 证据不足分不出胜负：保留冲突待确认（keep_both_pending），两条并存，挂“冲突待核实”并带核实动作与 ≤3 天期限。
- 禁止用“扩容/变更/笔误”等推断弥合冲突写成结论（2026-10-22 董事长令）。
- 事实被纠正/取代时，当天回写原条目，只写当日日志不算修订。

## 4. 分层 TTL

| 层 | 有效期 |
|---|---|
| 会话 | 1 天 |
| 约定 | 90 天 |
| 架构决策 | 长期（10 年） |

- 到期自动 EXPIRE；过期条目引用前须先复核，超 7 天未复核的单次快照只能说“旧记录、未核实”。

## 5. 定期整合

- 每周一次：识别过期 → 合并重复 → 归档/删除无用 → 重建索引。
- 整合须留记录（`consolidate.md`），删了什么、并了什么、为什么，一一列明。
- 精简前先备份，精简后核对无缺失再同步；分叉未点选前不得覆盖。

## 6. 知识库改动流程

- proposer 提 PR（改动说明 + 出处 + 影响面）→ reviewer 独立审核 → 通过才合并。
- reviewer 不得是 proposer；合并后须跑一次检索自检（改了的条目能被搜到）。

## 7. 门禁脚本用法

```
memory-gate.py init --kb-id <id> --workdir <dir>
memory-gate.py propose --state <state.json> --entry-id <id> --operation <ADD|CONFIRM|SUPERSEDE|RETRACT|EXPIRE|DELETE>
                     --actor <提议人> --content <文件> --tier <session|agreement|architecture>
                     --evidence <high|medium|low> [--key <去重键>] [--age-days <N>]
memory-gate.py check5 --state <state.json> --entry-id <id>
memory-gate.py resolve --state <state.json> --entry-id <id> --decision <keep_new|keep_old|keep_both_pending>
memory-gate.py apply --state <state.json> --entry-id <id> --reviewer <审核人>
memory-gate.py consolidate --state <state.json> --actor <席位> --artifact <整合记录>
memory-gate.py check --state <state.json>              # 全量校验，违规 exit 1
memory-gate.py status --state <state.json>             # 阶段表
```

- 任何写入推进前，gate 先验：五检查、冲突已消解、有效期已贴、脱敏通过、审核人 ≠ 提议人。
- gate 是唯一放行口径；口头“记下了”不算数，state 文件说了算。

## 8. 违规处理

- gate 拦截即事故信号：停手、报 twt、修状态后再推，不许绕过脚本改 state 文件。
- 同类违规第二次发生，加自动化防护；第三次按系统事故处理。

## 9. 参考

- Mem0（注意 2026 年改版后写路径改为 ADD-only，去重/冲突移到读时处理，本 SOP 的写时五检查是团队自定口径，不照搬 Mem0 新版）；Graphiti 双时态知识图谱（事实带有效期窗口，旧事实失效不删除）；Anthropic 记忆工具文档。

## 10. 版本

- v1.0 2026-11-10：首次固化；董事长令 8 类 SOP 全面落地之 7。
