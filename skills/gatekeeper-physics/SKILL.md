---
name: gatekeeper-physics
description: 物理门禁与防幻觉：能用数据库约束/哈希/时间戳物理拦截的，绝不用 Prompt 约束。含 source_tag 数据库级来源门禁、verify.py 证据门禁规格、时间戳防伪、熔断自动 triage 闭环。触发词：门禁、verify、防幻觉、source_tag、家长确认、证据校验、gatekeeper、REJECT。
---

# gatekeeper-physics（物理门禁与防幻觉）

## 核心理念
Prompt 约束不住模型，物理约束可以。凡涉及数据真实性、证据真实性、权限边界的规则，一律落到 DB 约束、文件哈希、时间戳校验、代码判据上，不许只写在提示词里。

## 一、数据库级 source_tag 门禁（防幻觉写入）
- learning_events / observations 加列 `source_tag`：取值 parent_confirmed / ai_observed / ai_inferred，NOT NULL、无默认值、无标签写入直接被 DB 拒绝。
- 触发器硬规则：写 parent_confirmed 前必须 `SET app.parent_channel='on'`；AI 通道物理上写不进"家长确认"，不依赖模型自觉。
- 与 evidence_level（L1/L2/L3 证据强度轴）是两个维度，严禁混用：source_tag 管"谁说的"，evidence_level 管"多可信"。
- 上线标准（三测全过）：AI 通道写 parent_confirmed 被拒、家长通道放行、无标签写入被拒。SQL 存档：child-ai-companion 目标文件 source-tag-gate.sql。

## 二、verify.py 证据门禁规格
- 契约字段 `acceptance_criteria`：[{type: file_exists / file_min_size, target: "artifacts/<相对路径>", expected}]；契约必须带 created_at。
- 证据包三件套（inbox/<task_id>/）：artifacts/（产物原件）+ receipts.jsonl（执行者真实回执）+ manifest.json 与 acceptance_self_check.json（由 gen_evidence.py 生成）。
- 回执硬规格：每行齐 tool_name / status / output_sha256 / created_at 四字段；每个 file_exists 目标必须有一条 tool_name=write_file 且 path 含目标相对路径的回执。
- manifest 做 sha256 完整性校验；target 只许包内相对路径，防路径逃逸。
- 时间戳防伪：回执 created_at 不得早于契约 created_at（容差 120 秒），防回执伪造/回填。
- **exit_code 判据已被删除并严禁恢复**：它能让契约夹带任意命令在门禁机上执行，门禁变后门（2026-10-20 PoC 实证后修复，改后 sha256 b1b7cb72…）。契约永远不许含可执行字段。

## 三、扫描与裁决（scan_inbox.sh）
- 系统 cron 每分钟扫描 inbox/；证据包缺任一文件判 INCOMPLETE 跳过（日志 "waiting for full bundle"），齐全才送 verify.py 裁决并写 logs/verify-<task_id>.json。
- scan_inbox 会把 pending 契约复制为规范副本 contracts/<task_id>.json；同 task_id 重跑前先删旧规范副本，防 schema 串味。

## 四、自动重试闭环（circuit_breaker.py）
- cron 每 5 分钟盯 logs/verify-*.json：同一工人 REJECT 满 2 次 → 隔离该工人待派契约 + 自动生成 triage 排障契约 + 告警写 state/alerts.log。
- 是"熔断 + 派修"，不是无限重试：最多自动 triage，不惊动董事长；修不好升级人工。

## 避坑警告
- 批量生成假回执 = 致命错误：证据真实性是门禁的命，宁可任务失败，不许造回执凑 PASS。
- 手搓 manifest/receipts 格式曾三连败（NO_VERDICT），证据文件只许 gen_evidence.py 生成。
- 旧教训：dispatcher 只看进程退出码就判 succeeded，是"假完成"的根因——验收只认 verify.py 的内容判据。

## 验证标准
- file_exists / file_min_size 判据 PASS 与 FAIL 回归各一次，行为与修复前一致。
- exit_code 类契约落 UNVERIFIED/NEED_HUMAN 且无任何命令被执行。
- source_tag 三测全过方可上线（见第一节）。

## 反思机制（2026-10-20 董事长令，OpenClaw 侧）
- 熔断不等于反思：circuit_breaker 隔离 + 派 triage 只是止血。同类任务（同 task_type 或同 REJECT 签名）证据包连续被打回 2 次时，OpenClaw 必须停派该类任务，并往 state/reflection_log.md 追加一条反思记录：REJECT 签名、根因假设、以及对 dispatcher 拆解 Prompt 或验收标准的具体修订提案。
- 修订提案由 TWT 审核后才许改 prompts/dispatcher.txt 与 contract-writer 技能；未审核前同类任务保持停派，不许无脑重派碰运气。
- 反思质量标准：提案必须指向具体字段/措辞（改哪一句、加哪条判据），不许写"加强质量"式空话。
