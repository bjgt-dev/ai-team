# Thorsten Ball 15 条 SOP 落地（2026-11-07）

> 董事长令（2026-11-07）：15 条全文加 SOP 落地，早就该交付。
> 全文来源见 thorsten-ball-15-fulltext-20261107.md（explainx.ai 2026-10-26 二手解读，不是 X 原帖逐字稿，X 域名被墙拿不到）。
> 本文件把每条落到我们团队的具体环节、改哪份 SOP/脚本、验收标准是什么。
> 待董事长定的 2 点（#14 写作者边界、#15 高爆炸半径名单）已列出，定之前按拟议口径执行，不得擅自扩大。

## 覆盖总览

| # | 规则 | 对应环节 | 落地状态 | 验收 |
|---|------|----------|----------|------|
| 1 | 用最新模型 | 模型池选型 | 已有，需收紧 | 见下 |
| 2 | slop tripwire | verifier 封堵 | 待补 | 见下 |
| 3 | 只给目标（Stop when / Must not） | 合同模板 | 待补 | 见下 |
| 4 | SDLC 全可见 | 工人可视性 | 待补 | 见下 |
| 5 | SDLC 工具化 | 杂活包装 | 待补 | 见下 |
| 6 | 盲测知识文件 | skill 季度盲测 | 待补 | 见下 |
| 7 | 一任务一 workdir | 任务隔离 | 已有，需强制 | 见下 |
| 8 | N 变体对比 | 模糊任务处理 | 待补 | 见下 |
| 9 | 无可辩驳的证据 | 证据规则 | 已有，需写进模板 | 见下 |
| 10 | 让 Agent 等你 | 异步批处理 | 已有 | 见下 |
| 11 | 定时、心跳 | cron 体系 | 已有 | 见下 |
| 12 | 别手工管上下文 | 一任务一线程 | 已有 | 见下 |
| 13 | 线程即记忆 | 交付物溯源 | 待补 | 见下 |
| 14 | 别让 Agent 替人写东西 | 写作者边界 | 待董事长定 | 见下 |
| 15 | 只审高爆炸半径 | 复核范围 | 待董事长定 | 见下 |

## Rule 1：用最新最强的模型

**我们现状**：模型池走免费档（OpenRouter 3 账号渠道 15/16/17 各 50 次/天、Zen 优先、网关兜底）。免费档里也有强弱之分，选型口径是"确定性阶梯"（越确定的越不用大模型）。

**落地**：
- 含糊任务、无法自动检查的任务：走池子里最强的可用免费模型（Tier-1），不走小模型。
- 批量机械活（重命名、格式化、机械重跑）：小模型即可，错了容易发现。
- 模型选型写进任务合同的 `input`，不留给工人自己猜。

**改哪**：`~/ai-team/docs/sops/code-dev-sop.md`，加一节"模型选型"；网关渠道表（free-channels）标注每个渠道的适用任务类型。

**验收**：抽查 5 个 CLAIMED 任务，含糊任务是否走了 Tier-1；机械任务是否走了小模型。抽查通过率 100%。

## Rule 2：slop tripwire（待补）

**我们现状**：没有 slop tripwire。Agent 产出的代码量在涨，没人盯。

**落地**：verifier 加三条 slop 检查，任一触发即 REJECT：
1. 测试运行时间比基线上升超过 20%（无合理解释）。
2. 新增依赖（无合同里写明的理由）。
3. 新增文件无人能讲明白用途（文件名无意义、内容无注释、无测试覆盖）。

**改哪**：`~/ai-team/scripts/c2-verifier-20261104.py`，加 `slop_tripwire` 检查函数；`~/ai-team/docs/sops/code-dev-sop.md`，加"slop 三指标"。

**验收**：构造 3 个 slop 样本（测试变慢、偷加依赖、无意义文件），verifier 全部拒绝；正常提交不受影响。

## Rule 3：只给目标，不给做法（合同加 Stop when / Must not）（待补）

**我们现状**：合同已有 `pass_line` 和 `forbidden`，但没有按 Thorsten Ball 模板拆成 Goal / Constraint / Stop when / Must not / Report。工人有时不知道"做到什么程度算停"。

**落地**：合同模板改为五段式，缺一段 gate 直接拒收：
- Goal：目标加数字（如"pytest 全绿"、"延迟降到 X ms"）。
- Constraint：约束（不花钱、不对外发布、不动哪些文件）。
- Stop when：停止条件（如"三项验收全绿"、"benchmark 连续三次达标"）。
- Must not：绝不能做的事（如"不弱化测试"、"不编造数据"）。
- Report：回执格式（结论、执行动作、验收证据、sha256）。

**改哪**：`~/ai-team/scripts/dispatch_task.py` 的合同模板；`~/ai-team/scripts/verdict-auto-dispatch.py` 的合同生成逻辑同步改；`~/ai-team/docs/sops/code-dev-sop.md` 的合同章节。

**验收**：新派 3 个任务，合同五段齐全；故意缺一段的合同被 gate 拒绝。

## Rule 4：让 Agent 看见完整开发生命周期

**我们现状**：工人（InstaBox worker-1）只能拉任务、跑测试，看不见生产日志、部署状态、队列深度、网关健康。看不见就编。

**落地**：给工人一个只读的"状态板"命令，一条命令看到全链路：
- 中央队列深度（READY / CLAIMED / DONE / QUARANTINED 各几件）。
- 网关健康（可用模型数、剩余额度）。
- 最近 5 件 DONE 任务的标题与结果。
- 本任务的合同、verdict、依赖。

**改哪**：新增枢纽脚本 `~/ai-team/scripts/hub-status.py`（只读，不写生产状态）；`~/ai-team/docs/sops/code-dev-sop.md`，工人开工第一步改为先跑 `hub-status.py`。

**验收**：工人在 workdir 里跑 `hub-status.py`，一次输出队列、网关、最近 DONE 全齐；不跑该命令直接开工的，verifier 在回执里扣一分。

## Rule 5：把生命周期里的重复活包成工具

**我们现状**：查日志、查队列、查网关、发回执，都是手工拼命令。

**落地**：每件重复杂活包成一个薄工具（脚本），不搞大平台：
- `hub-status.py`：状态板（Rule 4）。
- `task-evidence.py`：一键打包回执（交付物 + sha256 + 原始输出）。
- `queue-depth.py`：队列深度快查。

**改哪**：新增上述脚本到 `~/ai-team/scripts/`；`~/ai-team/docs/sops/code-dev-sop.md` 列出工具清单。

**验收**：三个工具都能在 hub 上一键跑通；工人回执用 `task-evidence.py` 打包的比例达到 100%（抽查 5 件）。

## Rule 6：盲测你的 AGENTS.md 和 skill 到底有没有用（skill 季度盲测）（待补）

**我们现状**：skill 和 AGENTS.md 越攒越多，从没盲测过到底有没有用。

**落地**：每季度一次盲测：
1. 挑一个文档声称能让它变容易的任务（如"按约定加一个新 API 端点"）。
2. 对照组：把知识文件拿掉，跑 3 次。实验组：带着知识文件，跑 3 次。
3. 对比：实验组有没有遵守约定、更快完成、更少 token？
4. 没效果的指引删掉或重写；有效果的留下来，写进知识库。

**改哪**：新增 SOP `~/ai-team/docs/sops/skill-blindtest-sop.md`；首轮盲测排进中央队列（任务号 `skill-blindtest-2026q4`）。

**验收**：每季度一份盲测报告，写清对照组与实验组数据、删了哪条指引、留了哪条。2026 年 Q4 首轮在 11 月底前完成。

## Rule 7：一任务一 workdir（已有，需强制）

**我们现状**：合同里已写 workdir（`~/code-agent-pilot/workdir/<task-slug>`），但工人有时不建 workdir、或多个任务共用一个目录。2026-11-07 实查：kids-iteration4 与 verifier-gates-234 被认领后 workdir 不存在。

**落地**：强制一任务一 workdir：
- 派队时 workdir 即锁定，写进合同。
- 工人认领后第一步必须是建 workdir，不建不算开工。
- 两个任务共用一个 workdir，verifier 直接 REJECT。

**改哪**：`~/ai-team/scripts/dispatch_task.py`，gate 检查 workdir 是否唯一；`~/ai-team/scripts/c2-verifier-20261104.py`，加 workdir 隔离检查。

**验收**：抽查 5 个 CLAIMED 任务，workdir 全部存在且互不相同；共用 workdir 的提交被拒绝。

## Rule 8：模糊任务要 N 变体对比（待补）

**我们现状**：模糊任务靠一轮轮聊天试，效率低。

**落地**：合同里对模糊任务加一条：产出 N 个变体加一张对比表，不许一次只试一种。
- 设计任务：10 个变体加一个带开关的 HTML 对比页。
- 性能 bug：4 个假设、每个假设一个 profiling 脚本、一个排名结果。
- 改 prompt：一个测试集、一个运行器、每个变体一张分数表。

**改哪**：`~/ai-team/docs/sops/code-dev-sop.md`，加"模糊任务 N 变体"一节；合同模板加 `variants` 字段（模糊任务必填）。

**验收**：抽查 3 个模糊任务，都有 N 变体加对比表；只交一个方案的模糊任务被打回。

## Rule 9：要无可辩驳的证据（已有，需写进模板）

**我们现状**：已要求证据（sha256、原始输出），但 "this is not evidence" 清单没写进合同模板，工人有时只报 "tests pass"。

**落地**：把证据规则写进合同模板和 AGENTS.md：

```markdown
## Evidence rules
- "Tests pass" alone is not evidence. Show the failing test before the change.
- For UI changes, attach a screenshot or short recording of the real flow.
- For performance changes, attach before and after numbers from the same machine.
- "It should work" and "this looks correct" are not evidence.
```

**改哪**：`~/ai-team/scripts/dispatch_task.py` 的合同模板；枢纽 `~/ai-team/docs/sops/code-dev-sop.md`；`~/ai-team/scripts/c2-verifier-20261104.py`，证据缺失即 REJECT。

**验收**：抽查 5 件 DONE 任务，回执都有 sha256 与原始输出；故意只写 "tests pass" 的提交被拒绝。

## Rule 10：让 Agent 等你，别你等它（已有）

**我们现状**：已走异步（中央队列、pull_once、看门狗）。但本席有时会盯着任务状态等。

**落地**：
- 派队一次写完整简报（目标、约束、证据要求），然后走开。
- 批量派队，不一次只派一件。
- 本席每小时最多查一次任务状态；卡住由看门狗报警，不靠人工盯。

**改哪**：不用改脚本，改本席工作习惯。`~/ai-team/docs/sops/code-dev-sop.md` 加一条"不盯屏"。

**验收**：连续 7 天，本席主动查任务状态的次数每天不超过 5 次；看门狗报警全部在 15 分钟内响应。

## Rule 11：定时任务、唤醒、心跳（已有）

**我们现状**：cron 体系已有：health_3q 每 30 分钟、gateway_canary、verdict 看门狗每 10 分钟、P95 每日 06:40、百炼提醒 2026-11-15、ASCS 续期提醒 2026-11-24。

**落地**：三类 job 齐全：
1. 部署后检查：每次部署后自动跑一次健康检查。
2. 每天错误汇总：每天 21:00 对账进晨报。
3. 依赖与发布监控：网关 canary、P95、额度守卫。

**改哪**：不用新增，补一张 cron 清单进 `~/ai-team/docs/sops/`，写清每个 cron 的用途与失败时写哪。

**验收**：cron 清单齐全；任一 cron 失败 10 分钟内写 outbox。

## Rule 12：别手工管理上下文（已有）

**我们现状**：已用压缩（compaction）、一线程一任务。合同是持久上下文。

**落地**：
- 一线程一任务，不跨任务复用线程。
- 跨任务都重要的细节写进合同文件，工人反复读合同，不读聊天记录。
- 不数 token，不手动管上下文。

**改哪**：不用改脚本，`~/ai-team/docs/sops/code-dev-sop.md` 加一条。

**验收**：抽查 5 个任务，都有独立合同文件；工人回执引用的是合同，不是聊天记录。

## Rule 13：线程就是公司记忆（待补）

**我们现状**：transcript 与 state 文件散着，没有链接到提交。

**落地**：
- 每个交付物的提交信息与回执里写清 task_id 与合同路径。
- state/ 目录即记忆库，task_id 可搜索。
- 队友问"为什么这么做"，凭 task_id 能找到当时的线程与决策。

**改哪**：`~/ai-team/docs/sops/code-dev-sop.md`，加"交付物溯源"一节。

**验收**：抽查 5 件 DONE 任务，回执里都有 task_id；凭 task_id 能在 state/ 里找到合同与 verdict。

## Rule 14：别让 Agent 替人写东西（待董事长定）

**我们现状**：没有明确的写作者边界。Ball 的线：Agent 只写调试步骤、事故报告、部分文档；不写给人看的、带语气与判断的文字。

**拟议口径（待董事长拍板）**：
- Agent 可以起草：调试步骤、事故报告、技术文档、代码注释。
- Agent 不可以写（须人写或人批）：用户-facing 文案、营销、对外发布、带语气与判断的文章。

**改哪**：董事长定后，写进合同模板的 `forbidden` 与 `~/ai-team/docs/sops/code-dev-sop.md`。

**验收**：董事长拍板边界清单；清单写进合同模板；抽查 5 件对外文案，全部有人批。

## Rule 15：只审高爆炸半径的代码（待董事长定）

**我们现状**：verifier 全量审，没有"只审高爆炸半径"的分级。

**拟议口径（待董事长拍板）**：只人工复核以下几类，其他有 Rule 9 的强证据即可略读或跳过：
- 数据库迁移和 schema 变更。
- 认证、权限、支付路径。
- 公开 API 契约。
- 所有人都依赖的少数中心模块（拟 5 个以内，名单待定）。
- 中央队列、派队器、verifier 本身的改动。

**改哪**：董事长定后，把名单写进 AGENTS.md 与 `~/ai-team/scripts/c2-verifier-20261104.py`（高爆炸半径改动强制人工复核）。

**验收**：董事长拍板高爆炸半径名单（5 个文件以内）；名单写进 AGENTS.md；抽查 5 件低爆炸半径改动，有强证据即放行，无需逐行审。

## 待补 6 项对照（董事长 2026-11-07 点名）

| 待补项 | 对应规则 | 落地位置 |
|--------|----------|----------|
| slop tripwire | Rule 2 | verifier 加三指标 |
| 合同加 Stop when / Must not | Rule 3 | 合同模板五段式 |
| skill 季度盲测 | Rule 6 | 新增 skill-blindtest-sop.md |
| 一任务一 workdir | Rule 7 | gate + verifier 强制隔离 |
| 模糊任务 N 变体对比 | Rule 8 | 合同模板 variants 字段 |
| 只审高爆炸半径代码 | Rule 15 | 待董事长定名单 |

## 待董事长定的 2 点

| # | 事项 | 拟议口径 | 定了之后 |
|---|------|----------|----------|
| 1 | #14 写作者边界 | Agent 只写调试步骤、事故报告、技术文档；不写用户-facing 文案 | 写进合同模板 forbidden |
| 2 | #15 高爆炸半径名单 | 5 个文件以内（迁移、认证、支付、公开 API、中心模块） | 写进 AGENTS.md，verifier 强制人工复核 |

## 一周落地计划（explainx 建议，套用到我们团队）

1. **Day 1**：合同模板改五段式（Rule 3），证据规则写进模板（Rule 9）。
2. **Day 2**：verifier 加 slop tripwire（Rule 2）与 workdir 隔离检查（Rule 7）。
3. **Day 3**：上线 `hub-status.py` 状态板（Rule 4/5）。
4. **Day 4**：跑第一次 skill 盲测（Rule 6）。
5. **Day 5**：模糊任务 N 变体进合同模板（Rule 8）。
6. **Day 6**：cron 清单补齐，部署后检查与每日汇总确认（Rule 11）。
7. **Day 7**：董事长定 #14 边界与 #15 名单（Rule 14/15），写进 AGENTS.md。

---

整理时间：2026-11-07。全文来源与逐字稿说明见 thorsten-ball-15-fulltext-20261107.md。
