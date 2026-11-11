# 代码开发 SOP：从需求到 DONE 的完整实施链

**日期：** 2026-11-06　**状态：** 固化版 v1（以枢纽 formal pipeline 实测为准）
**来源：** 枢纽 `state/pipeline-formal-20261106/`（REQUIREMENTS/DESIGN/ACCEPTANCE/TEST-RESULTS，已验收通过）+ 令 19 生成与执行分离链 + 本地高星技能（mattpocock TDD/implement-spec/code-review、superpowers 七阶段）+ Harness 方法论（local-harness-method）+ karpathy 四原则（local-harness-method/references/karpathy-four-principles.md，2026-11-06 已用）。
**原则：** 不从零发明——先盘现成的，再补缺的。

## 一、总览：八步链

```
需求 → Plan → 设计/合同 → 生成 → 测试 → 评审 → 验证 → 验收
(intake) (plan.md) (code_spec) (code_gen) (code_test) (code_review) (verifier) (DONE)
```

每步都有输入、动作、产物、验收、回滚；没有产物的一步等于没做。
Plan 是新补的一步：以前合同 JSON 直接写，缺一份人能读、能评审的计划——
现在 worker 接活前先填 `code-plan-template.md`，再动手。

## 二、plan.md 字段对标（文章那条 → 代码这条）

文章 beautiful-article 的 plan.md 是 Brief / Outline / Theme / Assets。
代码任务里这四个字段对应：

| 文章字段 | 代码任务里的对应 | 填什么 |
|---|---|---|
| Brief | 任务简述 | 一句话说什么问题 + 背景 + **不做什么**（边界比能力重要） |
| Outline | 实现大纲 | 拆解步骤、改哪几个文件、接口/函数名、每步的预期结果 |
| Theme | 技术选型与约束 | 语言、框架、风格、硬规（R1–R7，见 §四）、不许用的东西 |
| Assets | 输入资产 | spec 路径、锁定测试文件、依赖文件（hub 路径 + worker 相对路径）、参考文档 |

另加代码任务特有的四个字段：Contract（合同字段）、Tests（锁定测试清单）、
Review（评审要点）、Acceptance（什么叫 DONE）。模板见 `code-plan-template.md`。

## 三、每步的输入、动作、产物、验收、回滚

### 0. 需求（intake）

- **输入**：董事长或大管家的一句话需求 / 队列任务 / 事故复盘结论。
- **动作**：先判断进不进开发链——一次性修补、配置改动、纯查数不走本链；
  值得做的写成一句话需求 + 边界（不做什么）。
- **产物**：需求一句话 + 边界（写进 plan.md 的 Brief）。
- **验收**：需求能一句话说清、边界写明；说不清的打回，不许带着模糊需求进设计。
- **回滚**：无（还没产生任何东西）。

### 1. Plan（填模板，新增步骤）

- **输入**：需求一句话、相关文件位置、约束。
- **动作**：填 `code-plan-template.md` 全部字段；Outline 拆到文件级；
  Assets 逐项确认存在（不存在的先补，不许写“大概有”）。
- **产物**：`plan-<任务>.md`（人可读，评审用）。
- **验收（L3 内容层）**：Brief 一句话能说清；Outline 拆解无遗漏（输入的每一项都有去向）；
  Assets 每项都有真实路径；不做的写明了。
- **回滚**：plan 不合格就重写，不进设计；重写不碰任何代码。

### 2. 设计 / 合同（code_spec，sha 锁定）

- **输入**：plan.md。
- **动作**：写 8 字段合同 JSON（task、workdir、contract.output_path、
  contract.dependencies 等）+ code_spec（接口、行为、测试清单）；
  依赖必须声明在 `dependencies`（hub 路径 + worker 相对路径），
  不许测试运行时才发现缺文件。
- **产物**：合同 JSON + code_spec，sha256 锁定。
- **验收（L1 结构层）**：合同字段齐全；依赖逐项在枢纽存在；spec 与 plan 一致。
- **回滚**：合同改了就重新锁定 sha；旧 sha 的产物一律作废，不许混用。

### 3. 生成（code_gen，枢纽本地直调网关 Tier-1）

- **输入**：sha 锁定的 code_spec。
- **动作**：枢纽本地跑 `scripts/code-gen-20261106.py`，只产 JSON `{files}`；
  生成文件去 markdown 围栏、AST 解析校验 .py 文件；写 manifest（每文件 sha256）。
- **产物**：源文件 + manifest + receipt.json。
- **验收（L1+L5）**：退出码 0；AST 解析通过；manifest 逐文件 sha 对得上。
- **回滚**：code-gen 退出非 0 → 不写文件、不派发；修 spec 或生成器后重跑。
- **硬规**：worker-1 不持模型、不持上游密钥、不持网关 token（R6）；
  生成只在枢纽做，这是安全规则，不是临时缺口。

### 4. 派发与准备（dispatch + provision）

- **输入**：合同 JSON。
- **动作**：`pipeline-dispatch` 过 gate（exit 0）→ INSERT READY；
  `pipeline-provision` 按合同把文件 + 依赖送到 worker workdir，
  逐文件验 sha256，写 provision receipt。
- **产物**：central_tasks READY 行 + provision receipt JSON。
- **验收**：gate exit 0；provision ok=true；worker 侧 sha 与枢纽一致。
- **回滚**：provision 失败 → 任务 stays READY，receipt 记 ok=false，
  不认领、不重派、不静默重试。

### 5. 测试（code_test，worker-1 只跑锁定合同测试）

- **输入**：worker workdir 文件 + 锁定测试。
- **动作**：worker-1 的 pull_once（2 分钟 tick）认领 → 先跑 `hub-status.py` 看状态板（见 §九.3），再建 workdir → 跑 pytest（只跑合同锁定的测试，
  不许 worker 自己加测、改测）→ 提交 PULLONCE-BUNDLE-V1。
- **产物**：pytest 输出（exit 0）+ 提交包。
- **验收（L5 可执行层）**：pytest exit 0；包里只有合同内的文件；
  pytest-only 的空包（<500B 或以 `pytest_exit=0` 开头）直接拒收。
- **回滚**：测试失败 → RETRY_WAIT（带原文原因），retry <3 由 sweeper 提升，
  ≥3 进 QUARANTINED，只走 pipeline-disposition 人工处置，不许人工 SQL 硬改状态。

### 6. 评审（code_review，双轴，缺口见 §五）

- **输入**：生成文件的 diff（相对合同锁定点）+ code_spec。
- **动作**：两个轴并行审、不许互相污染、不许合并排名——
  Standards 轴：是否合乎项目已文档化的编码规范 + Fowler 异味基线（12 条）；
  Spec 轴：是否忠实实现 spec（缺的、多做的、看起来实现但实际错的，逐条引 spec 原文）。
- **产物**：两份评审报告（Standards / Spec 各一份）+ 修复清单。
- **验收**：两轴都无硬违反；判断类异味逐项有结论（修或注明不修的理由）。
- **回滚**：评审发现问题 → 最小切片修复（只修失败项），修完只重审失败的轴，
  通过的轴不重审；同一轴连续两次 FAIL 停下报父级。

### 7. 验证（verifier 五条封堵）

- **输入**：VERIFYING 行 + deliverables/ 下的产物。
- **动作**：c2-verifier 每分钟跑：产物在 quarantine/ 与 deliverables/ 两份
  字节一致、与 DB artifact_sha 一致、≥500B、非 pytest 空包，等五条封堵。
- **产物**：DONE 或 RETRY_WAIT（带原文原因）的 verifier 日志行。
- **验收**：只有 verifier 能翻 VERIFYING→DONE；任何人不许口头宣布 DONE。
- **回滚**：RETRY_WAIT 按 §五（5）处理；QUARANTINED 只走 disposition 脚本。

### 8. 验收（DONE + 证据 + 复盘）

- **输入**：verifier DONE 日志 + 产物 sha + 各步 receipt。
- **动作**：核对证据链（plan → 合同 sha → manifest → provision receipt →
  pytest 输出 → 评审报告 → verifier 日志），齐了才报 DONE；
  写一行复盘（什么有效、什么要改），失败模式进 lessons 库。
- **产物**：验收记录（任务 id + 证据路径清单）。
- **验收**：证据链缺一环即不算 DONE；复盘不写等于验收没做完。
- **回滚**：验收发现证据缺失 → 打回对应步骤补证据，不许补一句口头保证蒙混。

## 四、硬规（formal pipeline R1–R7，原样执行）

- **R1** 快乐路径零人工 SQL；处置只走 pipeline-disposition（记原因、单事务、
  reset retry_count，绝不碰 fencing_token）。
- **R2** 零人工传文件；全部走 pipeline-provision（逐文件验 sha，写 receipt）。
- **R3** 快乐路径零人工重启服务；改 queue_api/verifier 须备份 + py_compile +
  systemctl 重启 + 存活探针 + receipt。
- **R4** 零假 DONE；只有 c2-verifier 能翻 DONE；pytest-only 输出直接拒收。
- **R5** 依赖先声明，不许运行时发现缺文件；缺依赖是合同失败，不是现场补丁。
- **R6** worker-1 无模型、无 opencode、无上游密钥、无网关 token；生成只在枢纽。
- **R7** 每步写 receipt（task_id、sha256、时间戳、退出码）；没 receipt 的步骤等于没发生。

## 五、现状：已接通 vs 缺口（2026-11-06 实测口径，2026-11-09 更新 §五-2，如实写）

**已接通（有验收证据）：**
- 派发→准备→认领→测试→提交→验证→DONE 全链零人工跑通
  （pipeline-canary-20261106d，14:30:19 派发 → 14:34:01 verifier DONE，833 字节产物两份 cmp 一致）。
- 合同测试 `test-pipeline-contract-20261106.py`：CONTRACT_TEST_OK
  （queue_api 双写、verifier 读 deliverables/、pytest 空包拒收）。
- code-gen 去围栏 + AST 校验；provision 原子落盘 + sha 校验；disposition 脚本化。
- stall-scan 每分钟监视；verifier 五条封堵第 1、5 条已接。
- 评审已接通（审计模式，2026-11-09）：生产方 `scripts/review-producer-20261109.py`
  （双轴 Standards/Spec，产物 `state/code-review-20261107/review-<task>-{standards,spec}.json`），
  金丝雀 review-pipeline-canary-20261109 真模型 REVIEW-PASS，gate exit 0；
  queue_api 审计接线已随 live 服务生效（2026-11-09 08:42 重启，Main PID 3833945），
  首条 submit-review 审计日志 2026-11-09T00:48:03Z（`state/logs/queue-api-audit.log`，
  任务 a689c26b，REVIEW-BLOCKED rc=1，审计模式放行，任务最终 DONE）。
  触发为手动 CLI（code_gen 后、dispatch 前）；强制开启（ENFORCE=1）未做，属票据 2。

**缺口（未接通，不许宣称已做）：**
1. **verifier 封堵第 2、3、4 条未接**——只接了第 1、5 条。
2. **没有红绿 TDD 循环**——测试是合同锁定的，由 spec 定，不是实现者先红后绿
   一片一片写出来的（mattpocock TDD 的核心）。现状是“锁测试、跑测试”，
   不是“先写失败测试、再写最少代码”。小任务（add_one 级）可保持现状；
   复杂任务应在 code_spec 里按垂直切片拆测试，每一切片红→绿一轮。
3. **没有需求拆解的票据图**——implement-spec 的票据图（ticket graph + 前沿 frontier）
   我们没有；现在是一个合同一个任务，并行靠 claimer 并发上限 3。
   多文件大任务应先拆票据图再派发，否则一个任务过大、失败面太大。
4. **每日回归金丝雀 cron 未装**——`pipeline-canary-20261106.py` 已设计，
   装 cron 须董事长批（调度任务变更规则）。
5. **M4 golden-set-100 现 80/100，差 5 分**——禁止改题凑分，只修系统。
6. **长活无时限**——2026-11-06 M5 压缩窗口一跑 2 小时 40 分不吭声的教训：
   任何超过 30 分钟的步骤必须分段报进度 + 设时限，超时自动停下报父级，
   不许闷头跑（见 link-research-sop 的时间盒）。

## 六、五层审核如何落到代码链上（local-harness-method）

| 层 | 在代码链上审什么 | 审者 | 开在哪步 |
|---|---|---|---|
| L1 结构 | 合同字段齐、文件在合同路径、manifest sha 对得上 | 脚本 | 2、3、4 步 |
| L2 事实 | 每个“已完成”都有证据路径（文件+行号/日志+时间戳）；数字逐个能指出来 | 脚本比对 + 主 Agent 内联 | 8 步验收 |
| L3 内容 | 有没有静默丢失（输入每项都有去向）、判断有没有依据、边界守没守（R1–R7） | 主 Agent 内联 / 独立 Agent / 双顾问 | 1、6、8 步 |
| L4 风格 | 汇报结论先行 ≤300 字、脱名、不贴过程 | 主 Agent 内联清单 | 8 步汇报前 |
| L5 可执行 | 退出码、pytest 全绿、重跑幂等、回滚能用 | 脚本 | 3、5、7 步 |
| 终审三视角 | 编辑（它首先是它该是的东西吗）、视觉（移动端能读吗）、技术（能构建能打开吗） | 独立视角 | 只在对外交付节点 |

失败回滚：最小切片修复，修完只重审失败的层；同一层连续两次 FAIL 停下报父级；
有修复写 repair-log。

## 七、对照三家现成方法论（我们抄了什么、缺什么）

| 来源 | 抄到的 | 我们缺的 |
|---|---|---|
| mattpocock/skills：TDD | 锁定测试 + pytest 验收（形似） | 红→绿循环本身；接缝（seam）事先约定；垂直切片（一次一测一实现）；反模式清单（实现耦合、重言式、横切） |
| mattpocock/skills：implement-spec | 合同 + 派发 + 认领的任务流 | 票据图 + 前沿 frontier；实现者并行子代理 + 合并者；集成后统一 code-review 再合 |
| multica-ai/andrej-karpathy-skills：karpathy-guidelines | 四原则：先想再写 / 极简 / 手术式 / 目标驱动 | 原则未进 plan 模板检查项（待补：模板加四原则自查 4 条）；详见 local-harness-method/references/karpathy-four-principles.md；preflight.sh 应用审计 3/4 通过（见 local-harness-method/runs/20261106-karpathy-apply/report.md） |
| mattpocock/skills：code-review | 双轴思路（Standards/Spec 分开） | 评审已接通（审计模式，见 §五-2）；固定点（merge-base）+ 异味基线未成文 |
| obra/superpowers：七阶段 | 阶段划分与本链同构（brainstorm→spec→plan→TDD→review→merge） | brainstorm（发散）这一步没有；merge（合入）没有分支模型——我们是中心队列，无分支 |
| affaan-m/ECC | 未整库装；只参考“完整环境长什么样” | 安全扫描、记忆优化两块可单拎出来学；不整库装（与 opencode + 中央队列架构重叠） |
| garden-skills（beautiful-article） | plan.md 结构、Phase 硬检查点、多层审核 | plan.md 是本文新补的；Phase 检查点只在流水线里有，plan 层以前没有 |

## 九、Thorsten Ball 15 条落地（2026-11-07，代码已实现）

### 九.1 合同五段式（Rule 3）

合同除 8 字段外须含 `five_part` 对象，五段缺一 gate 直接拒收：
Goal（目标加数字）、Constraint（约束）、Stop when（停止条件）、
Must not（绝不能做的事）、Report（回执格式）。
派队脚本与 verdict 自动派队已同步生成五段式；模板见
`state/contract-mandatory-20261105/CONTRACT-TEMPLATE.md`。

### 九.1b 交付格式（verifier-gates-234 大管家条件 5，2026-11-08 起强制）

新 code 合同必须声明 `format: PULLONCE-BUNDLE-V1`（或 `require_bundle: true`），否则 taskcontract_gate.py 直接拒收；verifier 对声明 bundle 的合同收到 legacy 单个 .py 时直接 FAIL。2026-11-08 前已派队的 code 任务不受影响。

### 九.2 slop 三指标（Rule 2）

verifier（c2-verifier）对 code 任务加 slop tripwire，任一触发即 REJECT：
1. 测试运行时间比合同 `test_baseline_seconds` 上升超 20%。
2. workdir 出现合同 `allowed_dependencies` 之外的新依赖。
3. workdir 出现无意义文件（junk 文件名、空源文件）。
基线由合同声明；没声明基线的子项跳过，不误伤正常提交。

### 九.3 状态板与工具（Rule 4/5）

工人开工第一步：先跑 `scripts/hub-status.py`（只读），一次看到队列深度、
网关健康、最近 5 件 DONE、本任务合同与 verdict。不跑不算开工。
工具清单（都在 `~/ai-team/scripts/`）：
- `hub-status.py`：状态板（只读）。
- `task-evidence.py`：一键打包回执（交付物 + sha256 + 原始输出尾）。
- `queue-depth.py`：队列深度快查。

### 九.4 模糊任务 N 变体（Rule 8）

合同加 `variants` 字段：列表，每项须有非空 `name`。
模糊任务（需求不清、方案不唯一）必填：产出 N 个变体加一张对比表，
不许一次只试一种。设计任务 10 个变体加 HTML 对比页；性能 bug 4 个假设
各一个 profiling 脚本加排名；改 prompt 一个测试集加每个变体一张分数表。
目标明确的任务可传空列表 `[]`。

### 九.5 证据规则（Rule 9）

- "Tests pass" alone is not evidence. Show the failing test before the change.
- UI 改动须附真实流程截图或短录像；性能改动须附同一台机器的前后数字。
- "It should work" / "this looks correct" 不是证据。
- 回执须附原始输出与交付物 sha256；verifier 查证据 marker，缺失即 REJECT
  （.md 交付物除外：文档本身即证据）。

### 九.6 一任务一 workdir（Rule 7）

派队时 workdir 即锁定，写进合同；gate 检查 workdir 唯一，
另一非终态任务已用同一 workdir 的直接拒收；verifier 加隔离检查，
共用 workdir 的提交直接 REJECT。工人认领后第一步必须是建 workdir，
不建不算开工。注意：workdir 在工人机上，枢纽只校验唯一性，
不替工人建目录。

### 九.7 交付物溯源（Rule 13）

- 每个交付物的提交信息与回执里写清 task_id 与合同路径。
- state/ 目录即记忆库，task_id 可搜索：凭 task_id 能找到当时的合同、
  verdict、交付物与 verifier 日志。
- 队友问“为什么这么做”，凭 task_id 能找到当时的线程与决策，不许凭印象答。

## 八、相关资源

| 文件 | 内容 |
|---|---|
| `~/workspace/sops/code-plan-template.md` | 代码任务 plan 模板（先填再动手） |
| `~/workspace/sops/link-research-sop.md` | 链接深挖 SOP（收→挖→炼→验→迭） |
| `~/workspace/sops/beautiful-article-plan-sop.md` | 文章 plan 链 SOP（兄弟任务在写，写完对照） |
| 枢纽 `state/pipeline-formal-20261106/` | REQUIREMENTS/DESIGN/ACCEPTANCE/TEST-RESULTS（真源） |
| `~/workspace/custom-skills/local-harness-method/SKILL.md` | Harness 方法论 + 五层审核 |
| `~/workspace/custom-skills/local-tdd/SKILL.md` | TDD（红→绿、接缝、垂直切片） |
| `~/workspace/custom-skills/local-implement-spec/SKILL.md` | 票据图 + 实现者并行 + 合并 |
| `~/workspace/custom-skills/local-code-review/SKILL.md` | 双轴评审（Standards/Spec） |
