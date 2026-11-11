# AI 团队系统架构：核心概念与领域边界（2026-11-07）

> 总协调人第一阶段产出，待董事长确认核心概念与边界后，再启动第二阶段大规模调研。
> 本文件只定义概念与边界，不启动调研，不改生产。

## 一、核心概念（10 个）

| # | 概念 | 一句话定义 | 关键属性 |
|---|------|-----------|----------|
| 1 | 合同（Contract） | 任务的可执行契约，含五段式（Goal / Constraint / Stop when / Must not / Report）与验收标准 | 8 必填字段 + five_part；缺一 gate 拒收；verifier 只按合同判 DONE |
| 2 | 中央队列（Central Queue） | Postgres 表 central_tasks，任务的唯一真源，状态机 READY → CLAIMED → DONE / QUARANTINED / RETRY_WAIT | 租约 1200s、fencing、幂等键、重试上限 3 次 |
| 3 | 大管家（Butler） | 决策席：审方案、写 verdict、监督全链路；COO + 首席架构师 + 安全官 | 常驻监督循环（约 3 分钟一轮）；一轮只审最早一份定案请求；不写代码、不派单 |
| 4 | 总调度（Dispatcher） | 执行编排：拆单、派单、认领、追踪、重试、收回执 | OpenClaw 承载；不做业务定案；只按合同派队 |
| 5 | 工人（Worker） | 执行节点：拉活、做活、交回执；当前主力 worker-1（InstaBox） | pull_once 模式（≤20 分钟）；一任务一 workdir；只跑锁定合同测试 |
| 6 | 复核席（Verifier） | 独立验证：按合同查证据，证据缺失即 REJECT；只有 verifier 能把任务置 DONE | c2-verifier 确定性检查（五条封堵 + slop 三指标）；不同供应商模型复核 code/决策类 |
| 7 | Verdict | 大管家对定案请求的评审结论：通过 / 有条件通过 / 打回，附条件与理由 | 写进 state/butler-verdicts/<任务名>.json；通过后须进队，不进队即断链 |
| 8 | 模型网关（Model Gateway） | 模型接入层：OpenClaw gateway + newapi，渠道、额度、金丝雀 | 免费档为主；Zen 优先、网关兜底；可用池 ≥2 模型金丝雀 3/3 才算能用 |
| 9 | 看门狗（Watchdog） | 监督全链路的确定性脚本族：发现卡住就报警或自动修复 | stall-scan、long-task-sweeper、verdict-dispatch-watchdog、backup-watchdog 等；无 LLM |
| 10 | 决策队列（Decision Queue） | 四类必批事项的入口：花钱、对外发布、不可逆、只有董事长能给的输入 | state/decision-queue.json；four-category-gate 先过滤，不在四类的不进 pending |

## 二、概念关系

```
需求 → 大管家审 → Verdict（通过）→ 合同 → 中央队列（READY）
  → 工人认领（租约）→ 执行 → 复核席验（证据）→ DONE
  → 验收（董事长或独立复核）

模型网关 ──→ 大管家 / 总调度 / 工人（提供模型能力）
看门狗 ──→ 监督全链路（verdict 未派队、任务卡 CLAIMED、备份过期等）
决策队列 ←── 四类门过滤 ←── 大管家 / 架构审查 / 健康三问
合同 ──→ 贯穿全链路：派队凭合同、执行按合同、验证按合同、验收按合同
```

关键不变量：
- 无合同不入队；verifier 只按合同判 DONE，不认工人自述。
- 工人不持令牌、不持上游密钥；生成与执行分离。
- 大管家不在关键路径做执行，只做定案、审核、复核。
- 状态以中央队列为准，不以聊天、记忆、口头为准。

## 三、领域边界

### 包含（本领域内）

- 流水线：需求、设计、合同、生成、派发、测试、评审、验证、验收八步链
- 中央队列、租约、认领、fencing、重试、隔离、重投机制
- 大管家循环、verdict、八层审查、决策队列、四类门
- 工人协议（pull_once、workdir、checkpoint、租约续期）
- verifier 五条封堵、slop tripwire、证据规则、独立复核
- 模型网关、渠道、额度守卫、canary、金丝雀
- 看门狗体系（stall-scan、long-task-sweeper、verdict-dispatch-watchdog、backup-watchdog 等）
- 合同模板、门禁、五段式、N 变体
- 健康三问体检、状态板、执行台账
- SOP 体系（code-dev-sop、preflight-check、skill-blindtest 等）

### 不包含（本领域外）

- 三条商业线的具体业务内容：AI UGC 广告代做的具体客户与文案、国内认知知识产品的具体课程、AI 运营自我盈利的具体运营动作（只包含流水线如何交付它们，不包含业务本身）
- 外部 SaaS 产品本身：OpenRouter、Cloudflare、B2、NVIDIA NIM、SiliconFlow 等的具体实现（只包含我们如何接入、使用、验通它们）
- 儿童项目的具体教学内容：英语沉浸的教学法、数学苏格拉底问法等（只包含流水线如何交付软件迭代，不包含教学法本身）
- 董事长的个人事务、主聊之外的沟通渠道
- 枢纽机与工人机的操作系统、硬件选型（只包含流水线如何在这两台机上跑）

## 四、未规划冒出来的新概念 / 新结构评估

扫描范围：~/ai-team/scripts/（493 个文件）、~/ai-team/docs/sops/（11 份）、state/butler-verdicts/、state/decision-queue.json、中央队列任务。
判定标准：有必要 = 补上了原设计里确实缺的一环，且已被使用；没必要 = 与已有概念重复，或从未被使用，或是临时补丁该被根治方案取代。

### 留下来（补设计）

| # | 新概念 / 结构 | 冒出来原因 | 为什么必要 | 补设计动作 |
|---|--------------|-----------|-----------|-----------|
| 1 | verdict-auto-dispatch.py | 大管家写 verdict 后没人派队，1.5 小时断链 | 补上了 verdict → 队列的断链，是流水线闭环的缺口 | 补进 code-dev-sop §四，写清 verdict 通过后 15 分钟内自动派队 |
| 2 | verdict-dispatch-watchdog.py | 同上，看门狗先报警后升级为自动派队 | 与 1 互补：1 是执行器，2 是监督者；2 每 10 分钟扫一次，1 失败时 2 报警 | 与 1 合并说明：2 调 1，1 失败 2 报警；避免两份文档各说各话 |
| 3 | four-category-gate.py | 技术决策被推给董事长，浪费他时间 | 补上了决策队列的入口过滤，是“四类检查”落代码的机制 | 补进决策队列 SOP，写清 gate 关键词与 fail closed 口径 |
| 4 | hub-status.py | 工人看不见生产日志、队列、网关健康，只能靠猜 | Thorsten Ball Rule 4 落地，工人开工第一步先看状态板 | 已进 code-dev-sop §九.3；需补工人侧强制调用 |
| 5 | task-evidence.py | 回执打包靠手工拼命令，容易漏 sha256 | Thorsten Ball Rule 5 落地，证据打包工具化 | 已进 code-dev-sop §九.3；需补工人回执 100% 用它打包的抽查 |
| 6 | queue-depth.py | 队列深度快查 | 与 hub-status 有重叠，但轻量、单用途，留作快查工具 | 明确分工：queue-depth 只查深度，hub-status 是全量状态板 |
| 7 | skill-blindtest-sop.md | skill 越攒越多，从没盲测过有没有用 | Thorsten Ball Rule 6 落地，季度盲测是知识库保质期的机制 | 已建 SOP；首轮任务 skill-blindtest-2026q4 已进队 |
| 8 | long-task-sweeper-20261106.py | 任务卡 CLAIMED 不动，没人收尸 | 补上了租约过期回收的一环，与 stall-scan 互补 | 补进中央队列 SOP，写清隔离条件与重投流程 |
| 9 | supervisor-stall-scan.py | 同上，卡住的任务要被发现 | 发现卡住的任务，是看门狗体系的核心 | 已在用；需补与 long-task-sweeper 的分工说明 |
| 10 | auto_decisions（decision-queue.json 新列表） | four-category-gate 的副产物：可自决的项要有地方放 | 与 pending 分开，避免可自决项混进待董事长定的列表 | 补进决策队列 SOP，写清 auto_decisions 的含义与清理周期 |
| 11 | five_part（合同五段式） | 工人不知道“做到什么程度算停” | Thorsten Ball Rule 3 落地，合同缺一段 gate 拒收 | 已进 CONTRACT-TEMPLATE.md；需补 gate 校验的回归测试 |
| 12 | variants（合同 N 变体字段） | 模糊任务靠一轮轮聊天试，效率低 | Thorsten Ball Rule 8 落地，模糊任务须一次交 N 个变体加对比表 | 已进 CONTRACT-TEMPLATE.md；需补模糊任务的判定标准 |

### 干掉（或合并、停用）

| # | 新概念 / 结构 | 为什么干掉 | 处置 |
|---|--------------|-----------|------|
| 1 | cq-csv 的 claim 功能 | claim 任务却无执行体，抢认领后拖到隔离，是 dec-003/dec-004 的根因 | 已停用：crontab 第 31 行 cq_claim cq-timer 已注释（dec-004 选项 A）；脚本保留只做 sweep，不再 claim |
| 2 | queue-depth.py 与 hub-status.py 的重叠部分 | 两个工具都查队列深度，功能重叠 | 不删文件，明确分工写进 SOP：queue-depth 只做快查，hub-status 是全量板；下次重构时合并 |
| 3 | 493 个脚本里的历史 .bak 文件 | 备份文件散落在 scripts/ 下，与现行文件混在一起，找文件时干扰 | 不删（回滚要用），但须定期归档到 state/contract-backups/ 或 scripts/_bak/，不与现行文件同目录 |

### 待观察（暂不处理）

| # | 新概念 / 结构 | 观察点 |
|---|--------------|--------|
| 1 | 看门狗家族的膨胀 | 现有 17 个 watch/sweep/scan 脚本（backup-watchdog、deadletter_watchdog、loop-watchdog、p1_ticket_watchdog、review_trigger_watcher、seat_output_watch、brain_watch、disk_watch、instabox_billing_watch、cluster_watch 等），各管一摊，尚未有统一的看门狗注册表与去重机制 | 第二阶段调研时逐个核查：谁在跑、谁已停、谁重复 |
| 2 | ceo-seat/ 与 ceo-opencode.sh 的历史命名 | 角色注册表已明确“CEO 席”是历史称谓，现行架构无此职，但目录与脚本名仍用 ceo-seat/、ceo-opencode.sh | 不改名（改名牵连广），但新文档一律用“大管家 / 幕僚长”，不用 CEO 席 |

## 五、第二阶段待确认事项

董事长确认本文件（核心概念 10 个、领域边界、留/干掉清单）后，第二阶段再启动：
- 并行派出多个子代理，每个负责一个子领域（队列机制、verifier 机制、网关模型池、工人协议、大管家循环、看门狗体系、合同模板、SOP 体系），每个派发前写清上下文范畴与必读内容。
- 汇总成架构级设计文档：背后逻辑、结构性问题、改进方案、合作形式与合作标准。

---
整理时间：2026-11-07。总协调人第一阶段产出。
