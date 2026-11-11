# SOP（标准流程）— 枢纽共享版

- 真源：枢纽 `~/ai-team/docs/sops/`（本目录）。团队（工人、大管家、总调度）一律读这里。
- 本地 `~/workspace/sops/` 只是镜像，不作真源；新 SOP 先写枢纽再同步本地，改 SOP 同理。
- 规则来源：2026-11-06 董事长令“技能库上枢纽，本地只作镜像”，SOP 同理。
- 全团队严格执行（2026-11-10 董事长令）：以下 9 份带门禁机制的 SOP，本席、光头、Noesis、大管家、总调度、工人、复核席全部执行；文档不是机制——每份 SOP 都有门禁脚本（违规 exit 1）与测试脚本，口头“好了”不算数，state 文件说了算。

## 带门禁机制的 SOP（2026-11-10 固化，9 份）

| # | SOP | 文档 | 门禁脚本 | 强制执行点 |
|---|---|---|---|---|
| 1 | 简历撰写 | resume-sop.md | scripts/resume-sop-gate.py | 框架不冻结不许写文；撰写≠审核，四轮四人；收意见期间禁改文；验收只认董事长 |
| 2 | 协作派发 | dispatch-sop.md | scripts/dispatch-gate.py | 任务契约五项缺一不许派发；租约过期重派；attempt ID 去重，迟到回调拒收 |
| 3 | 安全权限 | security-sop.md | scripts/security-gate.py | 一 agent 一身份；越权调用拦截；高影响动作须未消费审批单；审计日志必记 |
| 4 | 评测验收 | eval-sop.md | scripts/eval-gate.py | 固定评测集 + 基线逐维度对比；任一维度退化不许发布；失败变新回归用例 |
| 5 | 生命周期 | lifecycle-sop.md | scripts/lifecycle-gate.py | 发布门三项证据缺一不许上线；kill switch 一键停用；退役须依赖清单清空 |
| 6 | 提示词与配置管理 | promptops-sop.md | scripts/promptops-gate.py | 语义版本号；PR 评审；CI 黄金集全过；灰度逐档；回滚=移动 production 标签 |
| 7 | 成本与额度 | cost-sop.md | scripts/cost-gate.py | 预算先行；70% 告警 / 85% 降级 / 100% 硬拦截；熔断冷却；高风险禁自动降级 |
| 8 | 记忆与知识库 | memory-sop.md | scripts/memory-gate.py | 写入五检查；冲突不靠最新获胜；分层 TTL；审核人 ≠ 提议人 |
| 9 | 事故与交接 | incident-sop.md | scripts/incident-gate.py | SEV 分级；先缓解后诊断；复盘 5 个工作日内且改进项带负责人+到期日；复盘后 runbook 必须更新 |

## 其他 SOP（文档版，机制待续）

- link-research-sop.md、beautiful-article-plan-sop.md、plan-template.md、code-dev-sop.md、code-plan-template.md、preflight-check.md、skill-blindtest-sop.md、thorsten-ball-15-sop-20261107.md、cron-inventory-20261107.md。
- preflight-check.md（开工前强制预检门）为硬规：新活开工前必过四问，低星（<1000★）视为没有。

## 验收证据（2026-11-10）

- 9 份带门禁 SOP 的测试均在本地与枢纽各跑一遍通过：简历 89、协作派发 50、安全权限 59、评测验收 78、生命周期 52、提示词 46、成本 37、记忆 46、事故 40，均为 0 failed；枢纽 smoke 全部全通；枢纽与本地镜像 sha 全部一致。
- 执行台账：state/execution-ledger.md，唯一写入口 scripts/ledger-append.sh（先备份再追加）。
