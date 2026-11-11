# AI 团队系统架构设计文档（2026-11-07）

> 领域：AI 团队系统架构——从需求到交付的完整流水线。
> 已定：10 个核心概念（合同、中央队列、大管家、总调度、工人、复核席、Verdict、模型网关、看门狗、决策队列）；领域边界见 `domain-concepts-20261107.md`。
> Rule 14：Agent 可写调试步骤、事故报告、技术文档、代码注释；不可写用户-facing 文案、营销、对外发布、带语气与判断的文章。
> Rule 15：只人工审数据库迁移、认证支付、公开 API、核心模块（≤5 个）、中央队列/派队器/verifier 本身；其他有 Rule 9 强证据可跳过。
> 本文档由第二阶段 12 个子代理并行调研合成（2026-11-07 19:21–19:34 CST），全部事实均在枢纽实测。

## 一、背后逻辑：系统如何运转

### 1.1 核心链路（需求 → DONE）

```
需求 → 大管家审（3 分钟/轮，实际约 15 分钟）→ Verdict（通过/有条件通过/打回）
  → 合同（五段式 + 8 必填字段 + forbidden）→ 中央队列（READY）
  → 工人认领（queue_api /v1/claim，租约 1200s）→ 执行（pull_once ≤20 分钟）
  → 复核席验（verifier 五条封堵 + 证据规则）→ DONE（只有 verifier 能置）
```

- **网关**供模型能力：Zen 优先、网关兜底；免费档为主（OpenRouter 3 账号、百炼、CF Workers AI 等）。
- **看门狗**监督全链路：42 个看门狗，统一注册表（双轨并存，待合并）。
- **决策队列**经四类门过滤：花钱、对外发布、不可逆、只有董事长能给的输入，才进 pending；其余进 auto_decisions。
- **合同**贯穿全链路：五段式（Goal/Constraint/Stop when/Must not/Report）缺一 gate 拒收。

### 1.2 关键机制

| 机制 | 实现 | 状态 |
|---|---|---|
| 中央队列状态机 | DB 触发器强制 8 状态转移 | 触发器为准，非文档 |
| 认领 | c2-executor（每分钟，租约 600s）+ queue_api（租约 1200s） | 双轨并存 |
| 租约 | 600s/1200s 双轨，fencing+1 | 文档只写 1200s |
| 幂等键 | `contract:<task_id>`（无轮换）、`timer:<name>`（静态键事故已部分修） | 终态行清键轮换已部署 |
| Verdict→派队 | verdict-auto-dispatch（每 10 分钟扫 verdict，15 分钟门） | 2026-11-07 18:08 才落地 |
| Verifier 五门 | code 跑全五条，llm_doc 只跑 1/5 | 代码已接线，生产零运行 |
| 健康三问 | health_3q 每 30 分钟，exit 0/1/2/3 | Q1 读 43h 旧池文件仍 PASS |
| 四类门 | four-category-gate.py，中英关键词 | 误报/漏报并存 |
| 定案代码门 | decision-code-gate.py | 全枢纽零调用点 |

## 二、结构性问题：跨领域共性（12 个子代理共发现 60+ 个问题，归纳为 8 类）

### 2.1 “有代码”≠“已 enforce”（最普遍，6 个子领域同时发现）

- **表现**：门禁脚本存在但未接进生产路径。verifier 五门代码已接线但生产零运行（GATES 收据 0 条）；M4 五硬护栏代码 11-07 才补齐，零生产接线、零 cron；M5 check 脚本无调用方；decision-code-gate 全枢纽零调用；dispatch-gate 未接 pipeline-dispatch（本次已接）；评审硬门未接 live submit；preflight 四门禁只在测试脚本引用。
- **根因**：扫描报告“90 项已补代码”被误读为已生效；SOP 标题写“代码已实现”与正文“待补”矛盾；无“接线验收”环节。
- **改进**：每个门禁必须有接线验收（生产日志有收据、回归样本被拒过）；新增 `gate-wiring-audit.py` 每日扫全部门禁的调用点与生产收据。

### 2.2 静态幂等键 + 终态行占位（中央队列、网关、看门狗三处同款）

- **表现**：`timer:<name>` 静态键被 DONE/QUARANTINED 旧行永久占用，gateway_canary 停滞 5.9h（13:31→19:24）；`contract:<task_id>` 无轮换；上午 d43a5688 手工清键，4 小时换行复发。
- **根因**：轮换靠各提交方自觉复制；timer_submit 只清终态行，非终态行（CLAIMED/RETRY_WAIT）超 deadline 不清；long-task-sweeper 疑似跳过 timer 类型；cron_flock 仅 8 槽位，当日 83 次 all_slots_busy。
- **改进**：DB 侧把部分唯一索引改为只覆盖非终态行（Rule 15 人工审）；timer_submit 轮换加过期非终态分支；sweeper 强制 QUARANTINED 超 deadline 的 timer 行；新增 gateway-canary-freshness-watchdog（每 10 分钟）。

### 2.3 健康感知链已断三环（网关、健康三问、看门狗三处同证）

- **表现**：gateway_canary 停滞 5.9h、Zen 零探针 80h、health_3q 读 43h 旧池文件仍报 PASS；唤醒体检 RED vs 三问 GREEN 打架；静默策略无代码实现，靠人工写 outbox。
- **根因**：Q1 只数池文件名额，不校验 mtime 与金丝雀新鲜度；brain_watch 5 分钟探针 11-04 已停用，改走 gateway_canary 后又断了；Q2 用 OR 逻辑（60 分钟事件 >0 或 24h DONE >0）；Q3 无物即过。
- **改进**：Q1 加 `gateway_canary_fresh` 子项（最新轮次 <60 分钟且逐模型 pass3=true）；恢复独立 Zen 轻量探针（每 30 分钟，不走中央队列）；新增 `health_3q_reporter.py`（记忆上一轮 exit，变色才写 outbox）。

### 2.4 认领路径三方说法不一（工人、中央队列、合同三处矛盾）

- **表现**：dispatch_task.py 注释称“worker-1 claims directly from Postgres”；实装 pull_once 走 `/v1/claim`；queue_api 注释称直接读库的是枢纽 c2-executor。workdir 两处创建点（dispatch 时、claim 时）都挂在假设上。
- **根因**：注释未随代码更新；worker-1 不持 DSN，经 queue_api HTTP 认领；真正直连库的是枢纽侧全部脚本。
- **改进**：以代码为准统一事实，改两处注释；pull_once code 分支开头加自建 workdir（客户端即最后防线）；central_tasks 加部分唯一索引防 workdir 竞态。

### 2.5 租约双轨与 deadline 脱节（中央队列、工人两处同证）

- **表现**：执行器 600s vs API 1200s，文档只写 1200s；执行器不续期，超 600 秒即可能被 sweep 打回 RETRY_WAIT 而原进程仍在跑；pull_once 只在执行前 heartbeat 一次，pytest（120s）/script（30s）后不再续租；dispatch 默认 deadline 4h，合同可要 1–48h。
- **根因**：无唯一租约定义；无后台 heartbeat 线程；无任务时长上限强制。
- **改进**：新建 `cq_common.LEASE_SECONDS` 唯一定义 + 执行器续期；pull_once 执行阶段起后台 heartbeat 线程（每 300s）；dispatch gate 拒收“预计 >20 分钟且无分段 checkpoint”的 code 合同。

### 2.6 旁路与杀开关无监控（合同、verifier、决策队列、M3/M4/M5 五处同款）

- **表现**：pull-pilot-server `/complete` 端点可直接置 DONE（dormant，但重启即重开）；ad-hoc psql 直插不过门禁；GATE-DISABLED 空文件存在即静默 bypass，无告警、无 TTL；decision-queue 是裸 JSON，无写入口强制，10/63 resolved 用第二套 schema（人工直接改 JSON）；M5 开关是声明式 + 离线校验，改 JSON 不自动拦。
- **根因**：“有代码”≠“已 enforce”的旁路形态；开关文件存在性判断，无监控。
- **改进**：删除 pull-pilot-server 旁路 + DB 触发器（只有 verifier 能置 DONE）；kill-switch 改 JSON 带 owner+reason+expires_at + 看门狗告警；decision-queue 加 schema 校验 + `decision-queue-guard.py` 每 30 分钟扫全表；dispatch 派发前调 `m5-switch-guard.py`。

### 2.7 证据与检查是弱正则 / opt-in（verifier、合同两处同证）

- **表现**：verifier 证据检查是弱正则（`=+`、`OK`、`pytest` 子串极易命中，不解析测试计数；`.md` 全豁免）；封堵 3/4 与 slop 1/2 默认 na（合同门禁不强制声明 test_command、test_baseline_seconds、allowed_dependencies，na 等效放行）；五段式只做非空检查（`goal: "g"` 单字母即通过）；Rule 14 forbidden 只查非空（`forbidden: "x"` 即通过）。
- **根因**：门禁只做形状校验，不做内容校验；最强检查是 opt-in。
- **改进**：门禁强制测试声明且 code 类 na 改拒；结构化证据解析（解析测试计数）；五段式加最小长度与关键词检查；gate 加 `FORBIDDEN_REQUIRED` 四要点内容检查。

### 2.8 注册表与清单双轨/过期（看门狗、SOP、M4 三处同款）

- **表现**：看门狗统一注册表双轨并存（快照 42 条 vs 精选 21 条，精选缺 21 条含 7 个在跑的）；SOP 本地镜像漂移（本地 6 份，缺 4 份新 SOP，sha 不一致）；预检台账双份并行（本地 13 行 vs 枢纽 1 行）；cron-inventory 一半是 "unknown"；M4 lessons 库 PG 表未建。
- **根因**：无唯一真源声明；无自动同步；无版本控制。
- **改进**：定枢纽为唯一真源，新增 `sop-mirror-check.py`（sha 比对 + outbox 告警）与 `sop-sync.sh`（scp 同步 + 验 sha），cron 每日跑；看门狗注册表合并为单一真源；cron-inventory 加 `--strict` 模式。

## 三、改进方案总表（按优先级）

### P0（正在发生的故障，已修或待修）

| # | 问题 | 改进 | 状态 |
|---|---|---|---|
| 1 | gateway_canary 停滞 5.9h | timer_submit 轮换加过期非终态分支 + sweeper 强制隔离 timer + 新鲜度看门狗 | L1 轮换已部署，19:30 已清键入队新行 42621aa5，待执行器认领刷新 |
| 2 | 3 任务卡 RETRY_WAIT 2h | retrywait-scan cron 已装（每 5 分钟 --apply） | 已装，3 件已回 READY |
| 3 | 幂等键静态事故 | DB 侧部分唯一索引只覆盖非终态行 | 待 Rule 15 人工审（DB 属主执行） |

### P1（结构性，需代码改动）

| # | 问题 | 改进 | 负责人 |
|---|---|---|---|
| 4 | “有代码”≠“已 enforce” | 新增 `gate-wiring-audit.py` 每日扫门禁调用点与生产收据 | 总调度 |
| 5 | 健康感知链断三环 | Q1 加新鲜度断言 + 恢复 Zen 独立探针 + `health_3q_reporter.py` | 总调度 |
| 6 | 认领路径说法不一 | 统一注释 + pull_once 自建 workdir + workdir 唯一索引 | 工人协议 |
| 7 | 租约双轨 | `cq_common.LEASE_SECONDS` + 执行器续期 + 后台 heartbeat | 中央队列 |
| 8 | 旁路与杀开关 | 删除 pull-pilot 旁路 + kill-switch JSON 化 + 决策队列 guard | 大管家审 |
| 9 | 证据弱正则/opt-in | 门禁强制测试声明 + 结构化证据解析 + 内容检查 | Verifier |
| 10 | 注册表双轨 | 唯一真源 + mirror-check + sync 脚本 + strict 模式 | 总调度 |

### P2（机制完善）

| # | 问题 | 改进 |
|---|---|---|
| 11 | Verifier 五门生产零运行 | 回归脚本 + 金丝雀（fake-1/2/3 在生产拒过） |
| 12 | M4 护栏零接线 | 晨报调 morning-metrics + 开场调 boot-quiz + DB 属主建 lessons_m4 表 |
| 13 | M5 开关离线 | dispatch 派发前调 m5-switch-guard + check 接 10 分钟 cron |
| 14 | 决策队列误报/漏报 | 词表补“决定/登录/批准/退款/收款”等 + SQL 上下文豁免 + 回归测试 |
| 15 | SOP 无版本控制 | 文件头加机器可读元数据 + `sop-version-check.py` + 旧版归档 `_bak/` |

## 四、合作形式与合作标准

### 4.1 合作形式

| 角色 | 职责 | 合作方式 |
|---|---|---|
| 董事长 | 定方向、定政策口径（Rule 14/15、领域边界）、批四类事项 | 只收结论/阻塞/下一步三段 ≤300 字；只问四类（花钱、对外发布、不可逆、只有董事长能给的输入） |
| 本席（twt/幕僚长） | 董事长唯一接口、定目标与优先级、组织团队、验交付、报结果 | 不替工人干活，不把中间选择推回董事长；可逆、低风险直接做，做完报结果 |
| 大管家 | 决策席：审方案、写 verdict、监督全链路 | 24 小时可调用角色；审方案先行，审过再进中央队列；不写代码、不派单 |
| 总调度 | 执行编排：拆单、派单、认领、追踪、重试、收回执 | 中央队列唯一真源；任务一律派给可达的主力工人，不绑死单人 |
| 工人（worker-1） | 执行节点：pull_once 拉活，一任务一 workdir，只跑锁定合同测试 | 自己写代码、不能只跑测试；不持令牌/上游密钥；闲置 30 分钟暂停、最长 480 分钟 |
| 复核席（verifier） | 独立验证：按合同查证据，只有 verifier 能置 DONE | 独立于工人；五条封堵 + 证据规则；REJECT 即 RETRY_WAIT |

### 4.2 合作标准

1. **规划与执行分离**：第一轮只给分析计划（背景、目标、不做、方案选项、评估标准、风险、回滚、需拍板点），董事长确认后再进执行轮；执行中偏离计划先停、先报。例外只有三类：董事长明确说直接做、已批执行令、已批的紧急可逆小修复。
2. **定案必须落地到代码**：任何定案（M3/M4/M5、机制、规则、流程），必须同时落地到代码（脚本、门禁、监控、测试），只写文档、只写 SOP 不算定案完成。验收标准：有可运行的代码 + 有测试证明它跑过、抓过违规；没有代码的定案等于没定，不得记为 DONE。门禁：`decision-code-gate.py`（定案标 DONE 前须引用代码文件加测试证据）。
3. **四类检查**：问董事长之前先过四类检查（花钱、对外发布、不可逆、只有董事长能给的输入）；不属于四类的，一律自己做，做完报结果，不许问。门禁：`four-category-gate.py`。
4. **五步流程**：需求、设计、开发、测试、验收，缺一步不算完。测试必须自己端到端实测，走用户真实使用路径；验收必须用户确认可用，才算完成。
5. **证据规则**：只认可复现证据（sha / 日志 / 进程状态 / 测试输出）；口头承诺不算数；READY / 已派 / CLAIMED 不等于完成，只认 DONE 加证据；用户发现问题 = 事故。
6. **开工前强制预检**：新活开工前必过四问（搜了哪、找到啥、用还是造、怎么验证），低星（<1000★）视为没有；台账 `preflight-ledger.jsonl`；派活简报首段必贴预检答案。
7. **执行台账**：每条董事长决策当天进台账，有负责人、有期限、有验收证据；只有证据能关单；每天 21:00 对账；过期未闭环置顶。
8. **复盘与防复犯**：同类错误第一次是问题，第二次必须落成机制（代码/门禁/监控/测试），第三次按系统事故；禁止用“我会记住”代替系统修复；文档不是机制。
9. **先搜后造**：动手前先找现成 skill/工具/官方能力；没有才自己造；找代码去 GitHub，找现成模型去 HF；低星直接略过。
10. **输出脱名**：聊天、文档、梳理输出里只称“董事长”，绝不写真实姓名；文件正文用角色词，不写死人名。

### 4.3 验收标准（整体验收）

- **全部环节**：需求、设计、开发、测试、验收五步齐全，缺一步不算完。
- **全部节点**：中央队列、大管家、工人、复核席、网关、看门狗、合同门禁、决策队列、四类门、健康三问、SOP，全部节点都要验到。
- **整体验收**：不是只修一个点，要系统性解决；不是只看一个任务 DONE，要看整条链路通了没；不是只看代码存在，要看生产跑起来了没（有收据、有日志、有测试证据）。

## 五、14 项技术项执行结果（2026-11-07 19:31）

| # | 项 | 状态 |
|---|---|---|
| 1 | 6 条新 cron | ✅ 已装（百炼 06:15、SiliconFlow 06:20、Maritaca 06:25、B2 03:45、muse-sync 06:40、Beszel 每 30 分钟） |
| 2 | gateway_canary 停滞 | ✅ 已恢复入队（新行 42621aa5 READY），待执行器认领刷新 |
| 3 | retrywait-scan cron | ✅ 已核实（早已装，每 5 分钟 --apply） |
| 4 | watchdog-20261106 cron | ✅ 已装（每 10 分钟 --live） |
| 5 | 21:00 台账对账 cron | ✅ 已装（0 21 ledger-check.py） |
| 6 | dispatch-gate 接 pipeline-dispatch | ✅ 已接线（拒绝即 exit 1、不插入） |
| 7 | lessons_m4 建表 | ⏳ 待 DB 属主（DDL 已写 `state/lessons_m4-ddl.sql`） |
| 8 | 角色注册表 | ✅ 已补 newapi 与 twt-core-backup |
| 9 | Rule 14/15 | ✅ 已执行（Rule 14 配置 + 合同模板增作者边界；Rule 15 配置 5 核心模块 + 5 类） |
| 10 | 8 条无日志 cron | ✅ 已加日志重定向（审计 cron_no_log=[]） |
| 11 | 26 孤儿看门狗 + .bak | ✅ 26 个已分类；.bak 273 个全归档到 `state/contract-backups/scripts-bak-20261107/`，scripts/ 下 .bak 归零 |
| 12 | SiliconFlow 渠道 | ⛔ 阻塞（Key 只在 Secure Vault，须父席执行 insert-channels.sql） |
| 13 | 评审硬门接 live submit | ✅ 已接线（审计模式），待大管家定强制（需评审记录生产方 + 金丝雀通过后设 `QUEUE_API_REVIEW_ENFORCE=1` 并重启 queue-api） |
| 14 | 每日回归金丝雀 cron | ✅ 已装（0 8 pipeline-canary-20261106.py，首跑 2026-11-08 08:00） |

Cron：96→106 行；备份 `state/crontab.bak-20261107-1931`；代码改动 4 个文件均 py_compile 通过。

## 六、待定事项（需大管家/DB 属主/父席定，非董事长）

1. gateway_canary 若 20:00 后 jsonl 仍未刷新，定是否人工干预（大管家）。
2. DB 属主执行 `state/lessons_m4-ddl.sql` 建 lessons_m4 表。
3. 父席用能取 Key 的路径执行 SiliconFlow 建渠道 SQL。
4. 评审记录生产方 + 金丝雀通过后，设 `QUEUE_API_REVIEW_ENFORCE=1` 并重启 queue-api（大管家）。
5. 主聊台账与枢纽 `state/execution-ledger.md` 的同步方式。
6. 05-supervisor-stall-scan.py 重复件与 cf-neuron-guard scripts/副本的去留。

## 七、子代理回执清单

| # | 子领域 | 回执文件 |
|---|---|---|
| 1 | 中央队列与租约机制 | `research/cq-lease-research-20261107.md` |
| 2 | 大管家循环与 verdict | `butler-verdict-research-20261107.md` |
| 3 | 工人协议与 workdir | （handoff 内文） |
| 4 | verifier 五条封堵与证据规则 | `research/verifier-five-gates-20261107.md` |
| 5 | 模型网关与 canary | （handoff 内文） |
| 6 | 看门狗体系 | `watchdog-survey-20261107.md` |
| 7 | 合同模板与门禁 | `contract-gate-research-20261107.md` |
| 8 | 决策队列与四类门 | `research/decision-queue-four-gate-20261107.md` |
| 9 | 健康三问 | `research/health-3q-20261107.md` |
| 10 | SOP 体系 | `sop-survey-20261107.md` |
| 11 | M3/M4/M5 落地状态 | `m3-m4-m5-landing-survey-20261107.md` |
| 12 | 14 项技术项执行 | `state/code-landing-14items-receipt-20261107.md` |

---
*本文档由第二阶段 12 个子代理并行调研合成，全部事实均在枢纽实测，未编造。*
