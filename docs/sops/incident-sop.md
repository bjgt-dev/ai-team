# 事故与交接 SOP v1.0（2026-11-10 董事长令，全团队执行）

> 真源：枢纽 `~/ai-team/docs/sops/incident-sop.md`；本地 `~/workspace/sops/incident-sop.md` 只作镜像。
> 机制：`~/ai-team/scripts/incident-gate.py`（门禁脚本，本地镜像 `~/workspace/tools/incident-gate.py`）。
> 原则：先缓解后诊断，复盘必改机制；文档不是机制——违反本 SOP 的推进会被 gate 脚本拦截。

## 0. 适用范围

- 本席、光头、Noesis、大管家、总调度、工人、复核席，全部执行。
- 任何事故（服务中断、数据丢失、越权操作、死信、磁盘告急）与席位交接一律走本 SOP。

## 1. 事故分级（SEV）

| 级别 | 定义 | 响应 | 升级 |
|---|---|---|---|
| SEV1 | 全停 | 立即，全员 | 立即报董事长 |
| SEV2 | 严重降级 | ≤30 分钟 | 1 小时未缓解报董事长 |
| SEV3 | 部分受影响 | ≤4 小时 | 当天未解决报 twt |
| SEV4 | 轻微 | ≤1 工作日 | 按周报汇总 |

- 分级由 twt 定；分级只许升、不许降（降级须大管家批）。

## 2. 七步链（顺序不可跳）

| 阶段 | 做什么 | 负责人 | 产出物（缺一不可） | 门禁 |
|---|---|---|---|---|
| 1 declare | 宣告：级别、影响、首条时间线 | twt | `declare.md` | 无级别不许建事故 |
| 2 triage | 分工：IC 指挥 + scribe 时间线 + comms 通报 | IC | `triage.md` | 三角色缺一不许缓解 |
| 3 mitigate | 先缓解：止血动作 + 记录 | IC | `mitigate.md` | 未缓解不许诊断 |
| 4 diagnose | 再诊断：根因 + 证据 | IC | `diagnose.md` | 无证据不许下结论 |
| 5 resolve | 解决：恢复验证 | IC | `resolve.md` | 未验证不许关单 |
| 6 postmortem | 无责复盘（P0/P1 须 5 个工作日内） | twt | `postmortem.md`（改进项带负责人+到期日） | 改进项缺负责人或到期日不许过 |
| 7 runbook | runbook 必须更新 | IC | `runbook.md` | 不更新 runbook 不许结案 |

## 3. 角色分工

- IC（指挥）：定优先级、拍板、盯时间线，只一人。
- scribe（记录）：时间线逐条记，只记事实不记猜测。
- comms（通报）：对外通报口径，只报已核实的信息。
- 三角色不得由同一人兼任（小事故可由 twt 兼 scribe，但 IC 必须独立）。

## 4. 交接清单（每次交接必含四项）

1. 未关告警
2. 进行中变更
3. 已知脆弱点
4. 待跟进项

- 四项缺一不许交接；交接文件须经接收方签字确认。

## 5. 复盘铁律

- P0/P1 事故 5 个工作日内无责复盘（门禁按 7 自然日计，含周末）。
- 复盘只谈机制不谈人；每个改进项带负责人 + 到期日，进执行台账。
- 复盘后 runbook 必须更新，否则事故不算结案。

## 6. 门禁脚本用法

```
incident-gate.py init --incident-id <id> --workdir <dir> --sev <1|2|3|4> [--age-days <N>]
incident-gate.py advance --state <state.json> --stage <阶段> --actor <席位> --artifact <文件>
incident-gate.py handover --state <state.json> --actor <席位> --artifact <交接文件> --to <接收方>
incident-gate.py check --state <state.json>              # 全量校验，违规 exit 1
incident-gate.py status --state <state.json>             # 阶段表
```

- 任何阶段推进前，gate 先验：顺序、三角色齐全、先缓解后诊断、复盘改进项带负责人+到期日、runbook 已更新、交接四项齐全。
- gate 是唯一放行口径；口头“修好了”不算数，state 文件说了算。

## 7. 违规处理

- gate 拦截即事故信号：停手、报 twt、修状态后再推，不许绕过脚本改 state 文件。
- 同类违规第二次发生，加自动化防护；第三次按系统事故处理。

## 8. 参考

- Google SRE Book（第 8 章 On-Call、事故分级、复盘）；PagerDuty 式 SEV1–SEV4 分级。

## 9. 版本

- v1.0 2026-11-10：首次固化；董事长令 8 类 SOP 全面落地之 8。
