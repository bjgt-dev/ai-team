# 协作派发 SOP v1.0（2026-11-10 董事长令，全团队执行）

> 真源：枢纽 `~/ai-team/docs/sops/dispatch-sop.md`；本地 `~/workspace/sops/dispatch-sop.md` 只作镜像。
> 机制：`~/ai-team/scripts/dispatch-gate.py`（门禁脚本，本地镜像 `~/workspace/tools/dispatch-gate.py`）。
> 原则：任务契约先行、租约认领、幂等去重、状态放持久存储；文档不是机制——违反本 SOP 的推进会被 gate 脚本拦截。

## 0. 适用范围

- 本席、光头、Noesis、大管家、总调度、工人、复核席，任何人经手协作派发一律走本 SOP。
- 派发指：把一个任务从派发方交给执行方（工人 / 另一席位），执行方认领、执行、回执，全程 state 文件说了算。
- 本 SOP 不管任务内容本身怎么写（内容质量走各业务 SOP）；只管派发、认领、执行、回执这条链不断、不重、不丢。

## 1. 四步 + 验收（顺序不可跳）

| 阶段 | 做什么 | 负责人 | 产出物（缺一不可） | 门禁 |
|---|---|---|---|---|
| 1 contract | 立任务契约：目标、输入版本、输出 Schema、预算、成功标准，五项齐全 | 派发方（twt / 大管家 / 总调度） | `contract.json` + 五项字段 | 缺一项不许派发 |
| 2 claim | 认领：执行方以 attempt ID 原子认领，生成租约（lease + 心跳 + 过期重派） | 执行方（工人） | `claim.json` + 租约 | 已有有效租约不许再认领；attempt ID 不许重复 |
| 3 execute | 执行：按契约执行，产出物落盘 | 执行方（租约持有者本人） | 契约约定的产出物 | attempt ID 须与租约一致；租约过期不许执行 |
| 4 receipt | 回执：派发方验收产出物、记账一次 | 派发方 | `receipt.json` | 迟到回调拒收；重复回执不得重复记账 |

- 阶段顺序：contract → claim → execute → receipt，缺前置不许推进。
- 重派须新 attempt ID：租约过期 / 执行方失联后重派，旧 attempt ID 作废，新认领换新 attempt ID，下游 execute/receipt 重置为 pending。

## 2. 任务契约（缺一项不许派发）

契约五项，`advance contract` 时五项必须全部非空，否则 gate 拦截：

1. **目标（goal）**：要做什么、不做什么，一句话可验收。
2. **输入版本（input_version）**：输入材料是哪个版本（如 `v1.0`、commit、文件路径 + sha）。
3. **输出 Schema（output_schema）**：产出物长什么样（文件名、格式、必填字段）。
4. **预算（budget）**：时间 / token / 次数上限，超预算先停先报。
5. **成功标准（success_criteria）**：怎样算通过，谁验收、按什么验。

- 契约写进 state 文件的 `contract` 字段；口头约定的契约不算数。
- 契约一旦立定，执行中改契约 = 重新立约：走新 attempt ID 重派，不许在旧租约上偷换目标。

## 3. 租约认领（lease + 心跳 + 过期重派）

- 认领须原子化：同一时刻一个 job 只能有一个有效租约；已有有效租约时，第二个认领直接被 gate 拦截。
- 租约字段：`attempt_id`（幂等键）、`holder`（认领者）、`claimed_at`、`last_heartbeat_at`、`expires_at`、`ttl_seconds`、`status`。
- 默认 TTL 1800 秒（30 分钟），`init --lease-ttl-seconds` 可改；长任务由执行方用 `heartbeat` 续租。
- 心跳：执行方在租约过期前发 `heartbeat --attempt-id <当前 attempt>`，续租一个 TTL；心跳 attempt 与租约不一致、租约已过期、租约已释放时，心跳一律拒收。
- 过期重派：超时未心跳，租约自动过期（`expires_at` 已过即过期，不用等谁来宣布）；过期后旧 attempt 作废，派发方以新 attempt ID 重派（claim 重做，下游重置）；过期后的旧执行、旧心跳、旧回执一律拒收。
- 认领者先查本机有无所需配置（按 2026-11-04 董事长定）：有则直接跑，无则一次性报缺哪项；不许先认领、再发现跑不了、占着租约不放。

## 4. 幂等键（attempt ID 去重）

- 每个认领一个 attempt ID，全局唯一，一经用过永不复用（记进 state 的 `used_attempt_ids`）。
- 重复 attempt ID 认领 / 重派：gate 拦截。
- 执行与回执必须带 attempt ID，且必须与当前租约的 attempt ID 一致；不一致 = 迟到回调，拒收。
- 重复执行结果不得重复记账：`receipt_count` 只能为 1；第二次回执（无论 attempt 是否相同）一律拦截，`receipt_count` 不得变 2。
- 网络重试、重复投递、迟到回调到达时，执行方 / 派发方先对 attempt ID，不对就拒，不许“先收下再说”。

## 5. 状态机放持久存储，不放模型对话里

- 状态机唯一真源是 state 文件（`dispatch-sop-state.json`），字段：`job_id`、`contract`、`stages`（四阶段状态 / actor / 产出物 / 时间戳 / attempt）、`lease`、`used_attempt_ids`、`receipt_count`。
- 模型对话、口头承诺、“我记得派过了”一律不算数；查状态只查 state 文件，`status` 命令是唯一口径。
- 手工改 state 文件 = 篡改：`check` 全量校验会抓获（顺序、前置、产出物存在、契约齐全、租约一致、attempt 登记、回执计数，任一项对不上即 VIOLATION）。
- state 文件写操作须原子落盘（先写临时文件再 rename），不许半截写入。

## 6. 门禁脚本用法

```
dispatch-gate.py init --job-id <id> --workdir <dir> [--lease-ttl-seconds <秒>]
dispatch-gate.py advance --state <state.json> --stage <阶段> --actor <席位> --artifact <文件>
        [--attempt-id <id>] [--contract-goal <目标>] [--input-version <版本>]
        [--output-schema <Schema>] [--budget <预算>] [--success-criteria <标准>]
        [--lease-ttl-seconds <秒>]
dispatch-gate.py heartbeat --state <state.json> --attempt-id <id> [--lease-ttl-seconds <秒>]
dispatch-gate.py check --state <state.json>              # 全量校验，违规 exit 1
dispatch-gate.py status --state <state.json>             # 阶段表 + 租约表
```

- 任何阶段推进前，gate 先验：顺序、契约完整、租约有效、幂等键、迟到回调拒收。
- gate 是唯一放行口径；口头“好了”不算数，state 文件说了算。

## 7. 参考（只借鉴思路，不照搬代码）

- Microsoft orchestration patterns（编排、租约、重试与幂等）：任务分派与认领、超时重派的通用模式。
- Anthropic orchestrator-worker（编排器—工人模式）：编排器拆任务、工人并行执行、结果汇总回编排器。
- A2A（Agent-to-Agent 协议）：跨 agent 任务委派、任务状态与结果回执的交互思路。
- MetaGPT / AutoGen / CrewAI / LangGraph：只借鉴多智能体协作思路，不照搬代码；低星项目（<1000★）视为没有。

## 8. 违规处理

- gate 拦截即事故信号：停手、报 twt、修状态后再推，不许绕过脚本改 state 文件。
- 同类违规第二次发生，加自动化防护；第三次按系统事故处理。

## 9. 版本

- v1.0 2026-11-10：Microsoft orchestration patterns、Anthropic orchestrator-worker、A2A 思路 + 团队派发实践；董事长令固化成机制。
