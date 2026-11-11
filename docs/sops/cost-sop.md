# 成本与额度 SOP v1.0（2026-11-10 董事长令，全团队执行）

> 真源：枢纽 `~/ai-team/docs/sops/cost-sop.md`；本地 `~/workspace/sops/cost-sop.md` 只作镜像。
> 机制：`~/ai-team/scripts/cost-gate.py`（门禁脚本，本地镜像 `~/workspace/tools/cost-gate.py`）。
> 原则：网关集中管密钥/路由/限额/预算/缓存/日志；文档不是机制——违反本 SOP 的推进会被 gate 脚本拦截。

## 0. 适用范围

- 本席、光头、Noesis、大管家、总调度、工人、复核席，全部执行。
- 任何模型调用、网关路由、额度申请一律走本 SOP；只走免费、不绑卡不付费（董事长令）。

## 1. 预算（先定预算再调用）

- 每 agent（或每 key）设日预算与月预算，预算文件须存在才许调用。
- 预算按免费额度定：不得超过该渠道免费档上限；用完即停，不产生超额费用。
- 预算变更须走审批（大管家），不许调用方自己改。

## 2. 阈值三档（日预算口径）

| 用量 | 动作 |
|---|---|
| ≥70% | 告警：记告警记录，通知 twt |
| ≥85% | 限流降级：记降级记录，切轻量模型或排队 |
| 100% | 硬拦截：调用必拒，等日切后恢复 |

- 三档记录须完整，事后可查；100% 之后再调用一律 exit 1。

## 3. token bucket 限速

- 每通道一个桶：容量 + 补充速率，突发调用超桶容量必拒。
- 桶按时间补充，不许手动灌桶；重试须等桶恢复，不许重试风暴。

## 4. 熔断器

- 连续失败 N 次（默认 3 次，预算文件里定）停用该通道，进入冷却期。
- 冷却期内调用必拒；冷却过后首次成功即复位。
- 熔断记录须写明失败原因与次数。

## 5. 备用切换

- 失败自动切备用模型：须有备用通道登记在先。
- 高风险任务禁自动降级到能力不足的模型：高风险标志的任务在熔断/降级时必拦截并记“转人工”，不许静默降级。

## 6. 成本看板

- 按 agent / 模型 / 任务统计用量（gate 提供 usage 汇总，数字须与记账一致）。
- 每日对账：用量与记账不一致即事故，停手查。

## 7. 门禁脚本用法

```
cost-gate.py init --agent-id <id> --workdir <dir> --budget-day <N> --budget-month <N>
                 [--bucket-capacity <N>] [--bucket-rate <N>] [--breaker-failures <N>]
                 [--cooldown <秒>] [--fallback <备用通道>]
cost-gate.py advance --state <state.json> --stage budget --actor <席位> --artifact <预算文件>
cost-gate.py charge --state <state.json> --actor <席位> --amount <N> --model <模型>
                    [--high-risk] [--failed]
cost-gate.py fallback --state <state.json> --actor <席位> --to <备用通道>
cost-gate.py usage --state <state.json>
cost-gate.py check --state <state.json>              # 全量校验，违规 exit 1
cost-gate.py status --state <state.json>             # 阶段表
```

- 每次 charge 前，gate 先验：预算存在、用量未超 100%、桶有余量、通道未熔断、高风险任务不自动降级。
- gate 是唯一放行口径；口头“还有额度”不算数，state 文件说了算。

## 8. 违规处理

- gate 拦截即事故信号：停手、报 twt、修状态后再推，不许绕过脚本改 state 文件。
- 同类违规第二次发生，加自动化防护；第三次按系统事故处理。

## 9. 参考

- LiteLLM 网关模式；BerriAI/litellm（100+ 供应商统一网关，虚拟 key + 预算 + 熔断 + fallback）；阿里云 AI 成本治理（身份→预算→告警→熔断四道）。

## 10. 版本

- v1.0 2026-11-10：首次固化；董事长令 8 类 SOP 全面落地之 6。本机制只做本地记账模拟，不产生任何真实费用、不调用任何真实计费接口。
