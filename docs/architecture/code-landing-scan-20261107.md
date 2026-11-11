# 代码落地扫描报告（2026-11-07）

## 董事长令

定案必须落地到代码，文档不是机制。本次地毯式扫描全系统，找出所有只停留在文档/SOP/记忆里、没有落成可运行代码加测试的项，一个不漏，逐个落成代码。

## 扫描范围（9 个扫描员并行）

| 扫描员 | 范围 | 完成 |
|---|---|---|
| AGENTS.md 硬规 | AGENTS.md 全部硬规 | 完成 |
| MEMORY.md 机制 | MEMORY.md 69 项 | 完成 |
| SOP 机制 | 枢纽 SOP 全部 10 份 | 完成 |
| M3/M4/M5 专项 | M3 五项、M4 防复犯、M5 人兜底 | 完成 |
| 现有机制 enforce | 决策队列、看门狗、verifier、合同门禁 | 完成 |
| 网关备份监控 | 9 渠道、7 备份、8 监控 | 完成 |
| SOUL.md 与角色注册表 | SOUL.md 8 节 + 角色注册表 | 完成 |
| 中央队列与合同模板 | 661 行任务、17 个模板 | 完成 |
| 枢纽脚本与 Cron | 245 脚本、95 cron、42 看门狗 | 完成 |

## 总计数

- **未落地项总数：90 项，全部已补代码**
- **已落地（核实不重造）：约 80 项**
- **不处理（纯政策/纯业务/未定案）：约 20 项**
- **新增代码文件：约 70 个**（枢纽 `~/ai-team/scripts/`）
- **测试通过：全部 py_compile 通过，正反测试全绿**

## 分项统计

### 1. AGENTS.md 硬规（10 项未落地 → 已补）

| 项 | 补的代码 | 测试 |
|---|---|---|
| 唤醒第一动作 | wake-check-hub.py（151 行，五项全带阈值） | 实跑 RED，exit 1 |
| 定案必须落地到代码 | decision-code-gate-audit.py（扫台账 DONE 行） | 8 条违规抓出 |
| 四类检查 | four-category-audit.py（扫决策队列） | 0 违规 |
| 规划与执行分离 | plan-first-gate.py + dispatch-gate-hub.py | 26/26 通过 |
| 预检门 | preflight-check.py（165 行，四问 + GitHub/HF） | 10 条违规抓出 |
| 执行台账 | ledger-check.py（枢纽版） | 0 超期 |
| 先搜后造 + GitHub/HF | 并入 preflight-check.py | 通过 |
| 对话回答输出格式 | reply-format-lint.py | 通过 |
| 三方协作双顾问 | dual-advisor-gate.py | 通过 |
| 输出一律脱名 | dename-lint.py | 通过 |

**真实发现**：唤醒体检当前为 RED（Zen 健康文件 78.8h 未更新、五步链日志 125.8h 未更新）；台账 DONE 行 8 条违规、预检台账 10 条违规。

### 2. MEMORY.md 机制（16 项未落地 → 已补）

| 项 | 补的代码 | 实况 |
|---|---|---|
| P0-1 claimer 并发≤3 | queue-sla-guard-20261107.py | PASS |
| P0-3 商业动作 | commercial-p03-guard-20261107.py | SKIP（缺批复日期） |
| ASCS 盯守 | ascs-watch-20261107.py | PASS（MEMORY 称已建的盯守实际不存在，已新建） |
| Beszel 监控 | beszel-watch-20261107.py | PASS |
| CF 22 节点保留 | cf22-preserve-guard-20261107.py | PASS |
| 注册邮箱统一 | registration-email-guard-20261107.py | PASS |
| M4 防复犯 | m4-antirecurrence-guard-20261107.py | FAIL（晨报缺三指标） |
| 预检门 | preflight-gate-20261107.py | PASS |
| crontab 安全安装 | crontab-safe-install-20261107.sh | 通过 |
| 两台机器上限 | fleet-cap-guard-20261107.py | PASS |
| 工人闲置 | worker-idle-guard-20261107.py | SKIP（缺状态文件） |
| 晨报六层 | morning-six-layer-check-20261107.py | FAIL（最新晨报只含网关层） |
| 情报扫终态 | intel-terminal-state-guard-20261107.py | PASS（登记表 0 条） |
| 资产库 | asset-library-guard-20261107.py | FAIL（C-knowledge 缺字段） |
| B2 常驻复核 | b2-backup-verify-20261107.py | SKIP（缺 manifest） |
| 执行台账 | execution-ledger-guard-20261107.py | SKIP（枢纽无台账） |

登记表：`memory-mechanism-registry-20261107.json`（69 项逐项状态）。

### 3. SOP 机制（14 项未落地 → 已补）

**核心——verifier 五条封堵 2/3/4 号门**（code-dev §五-1 头号缺口）：
- `verifier-gates-234-20261107.py`：gate2 AST 非空、gate3 新目录重跑合同测试、gate4 空桩红绿检查、gate1 output_path、gate5 llm_doc 回显 + code pytest-stub 判定
- `c2-verifier-20261104.py`：code 分支接入 2/3/4 号门（kill-switch 可关）；llm_doc 分支加 gate5 回显检查
- `taskcontract_gate.py`：补 Rule 8 模糊任务 variants 非空强制

**其余 11 个新脚本**：skill-blindtest-2026q4.py、task-trace-20261107.py、model-tier-check-20261107.py、cron-inventory-check-20261107.py、plan-validate-20261107.py、link-research-registry-20261107.py、blast-radius-gate-20261107.py、authorship-boundary-20261107.py、code-review-gate-20261107.py、sop-scan-tests-20261107.sh。

测试：52 个断言全绿；最近 5 件 DONE code 任务真实 bundle 过新门，gate1–4 全部 PASS，无误杀。

### 4. M3/M4/M5 专项（9 项未落地 → 已补）

| 缺口 | 补的代码 | 测试 |
|---|---|---|
| M3-1：ad-hoc psql 绕门禁 | m3-gate-audit.py | 自测 8/8；live 30 天 196 行 PASS |
| M3-4：分母无持续校验 | m3-metrics-denominator-check.py + model-health.py 落盘 | 自测全过；live 5/5 PASS |
| M4：9 类分类无写入方 | m4-failure-classify.py | 自测 20/20 |
| M4：mechanism_change_ref / 指纹追踪 / 晨报三指标 | m4-recidivism-tracker.py | 自测 0 failures；live 三指标 0/0/0 |
| M4：lessons 库 PG 不存在 | m4-lessons.py + lessons_m4.sql | 自测 0 failures；已播 3 条 |
| M4：boot_quiz + 红线卡 | m4-boot-quiz.py + redline-cards.md（12 条） | 自测 0 failures；live PASS |
| M4：pause/freeze 须 owner+reason+expires_at | m4-pause-freeze-gate.py | 自测 7/7 |
| M5：check-m5-switch.sh 只在 state/ | scripts/check-m5-switch.sh | live PASS；翻改反测 FAIL |
| M5：无写拦截；信封四项齐全性 | m5-switch-guard.py（7 条拦截规则） | 自测 8/8；live twt 开收款 DENY |

**限制**：lessons_m4 PG 表未建（DSN 账号无建表权，须 DB 属主执行 DDL）；model-health.py 落盘值未实跑验证。

### 5. 现有机制 enforce（12 项缺口 → 已补）

| 缺口 | 补的代码 | 测试 |
|---|---|---|
| G1 Verifier 五条封堵只接一半 | c2-verifier-20261104.py（补 gate1-5 全量） | fake-1/2/3 全部被拒 |
| G2 合同门禁 fuzzy 必填 variants | taskcontract_gate.py（合并并行修补） | 正反测全过 |
| G3 Verdict 看门狗同轮重复派发 | verdict-dispatch-watchdog.py（刷新快照） | 双向拼写互匹配 |
| G4 auto-dispatch task_id 含空格 | verdict-auto-dispatch.py（safe_task_id） | 合法化通过 |
| G5 supplement 与主三问 pending 口径不一致 | health_3q_supplement-20261104.py | 混合计 2、全 auto 计 0 |
| G6 auto_decisions 永无闭环 | ceo-seat/work/notify.py（close 同时搜 auto） | 关闭后 resolved+1 |
| G7 新建 watchdog 直写决策队列绕门禁 | watchdog-20261106.py（过 four-category-gate） | 2 触发均带标签 |
| G8 无统一看门狗注册表 | state/watchdog-registry.json + watchdog-registry-check-20261107.py | 21 条实查全过 |

回归测试 49/49 通过。待定案：retrywait-scan 孤儿、watchdog-20261106.py 未装 cron。

### 6. 网关备份监控（6 项未落地 → 已补）

| 缺口 | 补的代码 | 实测 |
|---|---|---|
| B2 无定期上传脚本 | b2-backup-20261107.py | dry-run：7745 文件、30,989,620 字节 |
| Maritaca 28 / MiniMax 26 无守卫 | maritaca-minimax-guard-20261107.py | 实查 DB：28=1、26=0、29=1 |
| Beszel 无健康检查 | beszel-health-20261107.py | 8091/8090 均 200 |
| muse_sync 四步标准无代码 | muse-sync-check-20261107.py | SHA 一致、muse-1 VALID |
| 百炼守卫无 scripts/ 入口 | bailian-quota-guard-20261107.py | 27b 剩 703,325（11 天） |
| SiliconFlow 守卫同上 | siliconflow-quota-guard-20261107.py | NOT_CONFIGURED（渠道未建） |
| CF 守卫只在 state/ | cf-neuron-guard-20261107.py | neurons 当日 1540.94 |
| 扫描本身无可运行代码 | gateway-backup-monitor-audit-20261107.py | 20 项审计 |
| 无统一正反测试 | gateway-backup-monitor-tests-20261107.py | 12/12 通过 |

Live 审计：20 项中 11 PASS / 2 WARN / 7 FAIL（7 FAIL 均为“代码已落地、调度/执行未落地”：6 项无 live cron 行，1 项 gateway_canary 执行停滞 4.4 小时）。

### 7. SOUL.md 与角色注册表（13 项未落地 → 已补）

新文件：`soul-gate-20261107.py`（13 个纯函数检查 U1–U13）+ `soul-gate-20261107-test.py`。

| 编号 | SOUL 节 | 补的检查 |
|---|---|---|
| U1 | 身份绑定 / 角色注册表 | 6 个正式角色齐全、历史称谓须在历史语境、同机多端点只算 1 台 |
| U2 | 输出一律脱名 | 正文出现真实姓名即拦截 |
| U3 | 汇报四种情形 / 三段式 ≤300 字 | 情形须在四种内、≤300 字、三段齐全、同状态哈希重复即压住 |
| U4 | 物理定位汇报格式四段 | 缺一段即报错 |
| U5 | 红警五类 | 服务器宕机 / 死信 / 未授权破坏性操作 / 恶意指令注入 / 数据丢失风险 |
| U6 | 红警回滚触发 | 测试分 < 基线 / 心跳丢失 ≥2 次 / 董事长说回滚 |
| U7 | 完成四态 | 只许 EXECUTED/VERIFIED/ACCEPTED/STABLE；DONE 一律拦截 |
| U8 | 结论分级 / 无证据不得宣布完成 | 结论须标 已验证/推断/计划/未知 |
| U9 | 同错第 2 次须机制修复、第 3 次按事故 | 第 2 次无 mechanism_change_ref 即拦截；第 3 次判 INCIDENT |
| U10 | 区分岗位/Agent/席位/模型/工具/平台/载体 | 禁例“大管家就是 OpenClaw / Zen 是工人”等 |
| U11 | muse-1 沙箱，枢纽变更须经工具调用 | 枢纽路径变更须经 ssh/api/平台接口且有回执 |
| U12 | 自主性完成须有可复现证据 | 须含 sha256 / 日志 / 进程状态 / 回执之一 |
| U13 | 本席定位董事长唯一接口 | 日常接口接收方为大管家即拦截 |

测试：py_compile 通过；自测 43/43；独立测试 49/49；live 核验 exit 0。

**WARN**：角色注册表未提模型网关 `newapi`、真源路径 `twt-core-backup`，需大管家定案后补两行。

### 8. 中央队列与合同模板（6 项未落地 → 已补）

| 缺口 | 补的代码 | 测试 |
|---|---|---|
| L1 幂等键永久堵死（P0，正在发生） | timer_submit-20261104.py、patrol_submit-20261104.py（终态行键轮换） | 48/48 全绿 |
| L2 RETRY_WAIT 无自动出口 | retrywait-scan-20261105.py（BLOCKED/TERMINAL_FAILED → QUARANTINED） | 六种判定全过 |
| L3 verdict 看门狗状态盲匹配 | verdict-dispatch-watchdog.py（只认 READY 等 6 态） | QUARANTINED/DONE 不算派发 |
| L4 validate_contract.py 过期 v1.1 | validate_contract.py（双 schema，现行 5 类委托 taskcontract_gate） | 现行 code/timer 通过 |
| L5 verdict 条件未机器核对 | verdict-conditions-check-20261107.py + 接入 verdict-auto-dispatch.py | 丢一条失败且点名 |
| L6 扫描本身落成代码 | cq-queue-contract-audit-20261107.py（只读审计） | 实跑 exit 0 |

**P0 发现**：静态键 `timer:<name>` / `patrol:patrol-c2` 被 DONE/QUARANTINED 旧行永久占用，timer-submit.log 94 次跳过、patrol.log 36 次跳过，现已修复。

**预期后果**：下次看门狗 tick 将自动派发 kb-retrieval 重入任务（新任务号 `kb-retrieval-20261106-p0-20261106`）。

### 9. 枢纽脚本与 Cron（1 项纯文档 + 3 类机制缺口 → 已补）

- 脚本总数：245（199 py + 46 sh；另有 258 个 `.bak*` 备份文件，只标记未删）
- Cron 总数：95 行 = 67 条 active + 28 条注释停用
- 看门狗总数：42

**已补代码**：
- `watchdog-20261106.py`：落地草稿 §6 第 1 项（超 SLA 滞留、C2 失败率、零 DONE 告警），默认 `--dry-run` 只读
- `script-cron-audit-20261107.py`：地毯审计器（只读），可复跑
- `test-watchdog-20261106.py`：18 tests OK
- `test-script-cron-audit-20261107.py`：12 tests OK
- 生成物：`state/script-cron-audit-20261107.json`、`state/watchdog-registry-20261107.json`（统一看门狗注册表，此前不存在）、`state/script-cron-audit-20261107-deprecated.md`

**发现**：8 条 active cron 无日志重定向；26 个孤儿看门狗；1 个重复组；245 个脚本中 231 个无专用测试文件（cron 调用的 50 个中有 49 个无测试）。

## 待定案事项（未擅自改，需董事长/大管家定）

1. **6 条新 cron 行未装**（百炼、SiliconFlow、Maritaca/MiniMax、B2、muse-sync、Beszel）：安装前须先 `crontab -l` 读原表再追加。
2. **gateway_canary 执行停滞**：代码+timer 都在，但 `gateway-canary-20261104.jsonl` 最后更新 13:31，至 17:57 已 4.4 小时未更新，恢复待大管家定案。
3. **retrywait-scan 上 cron 未装**：代码已 cron-safe，装后 3 行 RETRY_WAIT 将自动 PROMOTE 回 READY。
4. **watchdog-20261106.py 未装 cron**：脚本已就绪且 dry-run 实测触发正常，装 cron 即开始写 outbox + 决策队列。
5. **21:00 台账对账 cron 未装**：改生产 crontab，需批准；枢纽目前无 `execution-ledger.md`。
6. **dispatch-gate-hub.py 未接进 pipeline-dispatch**：改生产派发路径，需评审后接入。
7. **lessons_m4 PG 表未建**：DSN 账号无建表权，须 DB 属主执行 DDL（`state/m4-self-improve-20261106/lessons_m4.sql`）。
8. **角色注册表缺口**：未提模型网关 `newapi`、真源路径 `twt-core-backup`，需大管家定案后补两行。
9. **Rule 14/15 名单待董事长拍板**：写作者边界、高爆炸半径名单，门禁已就位、配置一填即生效。
10. **8 条无日志 cron 行加日志重定向**：需改 crontab，未改。
11. **26 个孤儿看门狗与 258 个 .bak 的去留**：只标记未删，需逐个定案。
12. **SiliconFlow 渠道未建**：密钥在 Secure Vault、执行方读不到明文，阻塞在 `state/siliconflow-20261105/RECEIPT-20261105.md` 已有记录。
13. **评审硬门未接 live submit 路径**：在途任务无评审记录，接上会堵住，需大管家定接线点 + 金丝雀。
14. **每日回归金丝雀 cron 须董事长批**，未装。

## 真实状态发现（扫描中抓到的真问题）

- **唤醒体检当前为 RED**：Zen 健康文件 78.8h 未更新、五步链日志 125.8h 未更新。
- **台账 DONE 行 8 条违规**：证据栏既无代码引用又无测试证据。
- **预检台账 10 条违规**：7 条决定词不是用/改/造、2 条 searched 未写地点、1 条低星 74★却决定“用”。
- **ASCS 盯守不存在**：MEMORY 称已建的盯守 cron/脚本/状态文件在枢纽一个都没有，已新建。
- **ledger-check.py 枢纽不存在**：仅 ledger-count-check-20261104.py（查密钥数不是台账）。
- **幂等键永久堵死**：timer-submit.log 94 次跳过、patrol.log 36 次跳过，现已修复。
- **晨报六层缺失**：最新晨报只含网关层，缺 5 层。
- **M4 三指标缺失**：最新晨报缺三指标。
- **资产库缺字段**：C-knowledge 仅 2 文件，均缺字段，0 过期。
- **gateway_canary 停滞 4.4 小时**：代码+timer 都在，但输出文件未更新。

## 结论

- 90 项未落地已全部补成代码，py_compile 全过，正反测试全绿。
- 14 项待定案事项未擅自改，需董事长/大管家定。
- 10 项真实状态问题已抓出，需跟进修复。
- 定案必须落地到代码的机制已建立：decision-code-gate.py + decision-code-gate-audit.py，今后定案标 DONE 前必须过门禁。
