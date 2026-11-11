# Harness 应用表：方法论套到全部重复流程

排序逻辑：收益 = 频率 × 每次手搓的痛 × 可判定程度。排前面的先包。

## 1. 情报 3 小时增量扫 → 筛选 → 登记 ★已落地

- **现状**：枢纽 `scripts/intel_pipeline.py --incremental`，cron 每 3 小时跑；6 个源（GitHub Trending、V2EX、Bilibili、YouTube、Linux Do、X），去重 `state/intel-seen.json`，免费命中写 outbox 报警，技能命中追加 `state/skills-registry.md` 第七节。X 登录被拦（缺正确的用户名）、Linux Do 偶发 403。
- **包成 Harness 后**：`local-intel-harness`（SKILL.md + `scripts/run-incremental.sh` + `scripts/check-run.py`）。采集仍走生产脚本（不动它），Harness 只做编排 + 分流复核 + 验收：每轮产出 `runs/<时间>/report.md`，逐条结论 DONE / BLOCKED / 待核；源失败必须显式列出，不静默跳过。
- **现在缺什么**：无。已落地，实测见 `local-intel-harness/runs/`。
- **收益**：每天 8 轮，全自动，零手搓。

## 2. 模型 S2 / 金丝雀评测

- **现状**：`scripts/gateway_s2_medium-20261105.py`（S2 三题，模型列表写死）、`scripts/gateway_canary-20261104.py`（金丝雀）、`gateway_canary_tier2-20261104.py`；结果写 `state/method-10-eval-routing/`，入池要人工看分再改 `gateway-pool.json`。
- **包成 Harness 后**：`local-model-eval-harness`。输入：一份评测清单（模型、题集、通过线）；脚本：跑题 → 计分 → 写 verdict JSON（模型、得分、延迟、通过/不通过）；验收：分数可复算、超时/429 单独列、不通过不写池；检查点：** verdict 全绿才允许改 `gateway-pool.json`，改前备份**。
- **现在缺什么**：模型列表与通过线写死在脚本里（要抽到 config）；没有机器可读的 verdict 文件；入池是人手改，没有门。
- **收益**：每周跑、每次换模型都要重走一遍；评测作弊（改题凑分）靠门禁防。

## 3. 席位切换交接（@唤醒拉六件）

- **现状**：新席位发 `@唤醒` 拉云端六件（SOUL.md、IDENTITY.md、USER.md、MEMORY.md、AGENTS.md、TOOLS.md）；交接说明在枢纽 `state/dashboards-handover-20261106.md`；董事长刚要求无缝切换。
- **包成 Harness 后**：`local-seat-handover-harness`。脚本：`scripts/switch-check.sh`——六件是否拉到且为最新、枢纽 SSH 是否通、三问体检是否全绿、三个看板快照日期是否在 24 小时内、cron 是否在跑；验收：逐项 PASS/FAIL 清单，全绿才算交接完成；失败项自动列修复命令，不让人现想。
- **现在缺什么**：没有 switch-check 脚本；六件的新鲜度没人验（旧快照超 7 天未复核只能当旧记录）；看板刷新靠每日 cron，切换当天不一定已刷。
- **收益**：切换是低频高危动作，一次断链全队停摆；董事长已明确要无缝。

## 4. 免费渠道注册与验证（羊毛 SOP）

- **现状**：SOP 已定（核实 Key 能用 → 加进网关 → 入免费渠道总表），注册一律 registration@example.com、不绑卡；但注册要走浏览器（人/浏览器任务），验证与入库是人手跑。
- **包成 Harness 后**：`local-free-channel-harness`。拆两段：浏览器段（注册、收码、取 Key，有明确的输入输出交接格式）；脚本段：`scripts/verify-key.sh <供应商> <key>` 直连探通 → `scripts/add-channel.sql` 写库（先查 id 不撞号，status 先 0，探通后改 1）→ 写总表。验收：每个 Key 必须有直连 200 证据才入库；注册失败的供应商记原因，不重复试。
- **现在缺什么**：verify 与 add-channel 是人手拼命令，没有固定脚本；总表是人手写，没有“入库即登记”的钩子。
- **收益**：每批线索 5–10 条，手工最容易丢 Key、撞 id、把没探通的写进生产。

## 5. 看板每日刷新

- **现状**：Muse 侧 cron `dashboards-daily-refresh`（每日约 08:00）读枢纽数据源、`artifact.edit` 刷新三个看板；交接文档在枢纽；但每次刷新是 Agent 现场发挥，没有固定取数清单。
- **包成 Harness 后**：`local-dashboard-refresh-harness`。脚本：`scripts/fetch-snapshot.sh` 按看板取数（gateway-pool.json、channels 表、logs 7 天聚合、P95、巡检 status、skills-registry、kids 迭代状态）写一份 `snapshot-<日期>.json`；验收：每个数字都能追到源文件行数、快照日期必改、没变化的区块不动；检查点：任一数据源读不到，留旧值并注明，不编数字。
- **现在缺什么**：取数清单只在 cron 的 body 文字里，没有可执行的 fetch 脚本；没有 snapshot 文件，数字对不上时无法复核。
- **收益**：每天跑，数字错一次信任就掉一次；快照文件是复核的证据。

## 6. 备份与恢复演练

- **现状**：备份脚本有（`scripts/backup_amd_proxy.sh`、`scripts/backup_assets.sh`），每天跑；恢复演练没做过（重建演练只停留在计划里），备份内容是否齐全没验过。
- **包成 Harness 后**：`local-backup-drill-harness`。脚本：`scripts/restore-drill.sh`——在隔离目录按备份包重建、校验文件数与 sha、量 RTO（恢复耗时）；验收：清单逐项对得上、服务能起、RTO 写进报告；检查点：演练失败不碰生产，只写报告 + 待修项。
- **现在缺什么**：没有 restore-drill 脚本；没有演练排期；备份内容清单没有逐项核对过。
- **收益**：低频，但一旦真丢数据就是灾难；演练是唯一能证明备份有用的办法。

## 7. 视频样片生产（G2 / UGC）

- **现状**：G2 首批 3 件（落地页文案 + 3 条样片分镜）已过独立复核；生产是人工写分镜、人工剪；motion-showreel 的确定性渲染管线还没在我们这边跑通过。
- **包成 Harness 后**：`local-video-sample-harness`，直接套 motion-showreel 的结构：brief.md（对标、结构、风格、记忆点）→ 风格帧（3 个方向各 1 张，等确认）→ `render(t)` 确定性渲染 → `inspect.mjs qa` 技术验收（尺寸/帧率/时长/黑帧/响度全绿）→ 审片（最严重的 3 个问题）→ 交付。检查点：风格帧与成片两次停下等确认，不跳过。
- **现在缺什么**：渲染管线（Node + Playwright + ffmpeg）没在 worker 上跑通过；没有 brief 模板；没有技术验收脚本；配乐/TTS 供应商未定（MiniMax 额度已耗尽）。
- **收益**：商业主线，单件价值最高；但前置基建最重，所以排最后——先把上面六条的 Harness 跑顺，再攻这一条。

## 已入库资产（2026-11-06，扫→用→资产库）

每条资产三样齐才算入库：技能文件 + runs 实测证据 + 登记表“已用”状态。标签按资产库口径打（场景 / 技能类型 / 模型层 / 成本 / 分级 / 复核周期），以后先查资产库再去找新的。

### A1. karpathy 四原则（开发指导页）

- **解决了什么**：代码任务里常见的过度复杂、顺手乱改、目标不清（M5 跑 2 小时 40 分不吭声、cron 整表覆盖都属此类）。
- **验证数字**：`preflight.sh` 应用审计 3/4 通过——P2 极简（41 行只做两件事）、P3 手术式、P4 目标驱动（check 返回码 0/1 可验证）通过；P1 小扣分：python3 存在性假设未显式检查。证据：`local-harness-method/runs/20261106-karpathy-apply/report.md`。
- **以后什么活优先用它**：所有代码任务的 plan 评审、code_review 前自查；plan 模板待补四原则自查 4 条。
- **标签**：场景=代码开发；技能类型=方法论；模型层=全层；成本=0；分级=核心；复核=季度。

### A2. anthropics 文档技能 local-pdf / local-docx / local-xlsx / local-pptx

- **解决了什么**：pdf/docx/xlsx/pptx 的读与转格式，以前靠手搓或零散脚本，没有固定技能。
- **验证数字**：11/11 通过——pdf：42 页（25,911 字）+ 30 页（20,458 字）真实文档读出，合并 72 页；docx：真实 SOP 内容 166 段回读，转 PDF 8 页；xlsx：真实调用量数据，公式重算 total_errors=0，合计回读 16216，转 CSV 14 行；pptx：3 页回读，validate 全过，转 PDF 3 页。证据：各技能 `runs/20261106-verify/report.md`。
- **以后什么活优先用它**：任何以 .pdf/.docx/.xlsx/.pptx 为输入或输出的活（读报告、转格式、做表、做 deck）。
- **标签**：场景=文档处理；技能类型=工具技能；模型层=全层；成本=0；分级=核心；复核=随上游更新。环境差：pypdf、defusedxml 本机原无（venv 已补装）；pandoc 缺失，docx 回读改用 python-docx；生产上 worker/枢纽侧须先确认这三个库可用。

### A3. caveman 省 token（local-caveman）

- **解决了什么**：输出 token 贵、汇报啰嗦。
- **验证数字**：事实在上下文中时，输出 token 省 75%（113→28），事实全保留（402、503、三次都在）；事实不在上下文时，输出 token 省 92%（364→29）但断言假事实（把停用原因编成“安全漏洞/性能问题”）。证据：`local-caveman/runs/20261106-compare/report.md`。
- **以后什么活优先用它**：汇总工具输出、复述刚读过的文件、按清单汇报状态。**禁用于**模型靠记忆回答事实问题。
- **标签**：场景=输出压缩；技能类型=风格技能；模型层=全层（输出侧）；成本=0（省 token）；分级=备用（限事实在上下文时）；复核=季度。
