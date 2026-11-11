# 生命周期 SOP v1.0（2026-11-10 董事长令，全团队执行）

> 真源：枢纽 `~/ai-team/docs/sops/lifecycle-sop.md`；本地 `~/workspace/sops/lifecycle-sop.md` 只作镜像。
> 机制：`~/ai-team/scripts/lifecycle-gate.py`（门禁脚本，本地镜像 `~/workspace/tools/lifecycle-gate.py`）。
> 原则：每个 agent 有负责人、有明确生命周期状态、有阶段迁移证据；文档不是机制——违反本 SOP 的推进会被 gate 脚本拦截。

## 0. 适用范围

- 本席、光头、Noesis、大管家、总调度、工人、复核席，全部执行。
- 任何 agent（席位、工人、网关、巡检、定时任务）上线、变更、退役一律走本 SOP。

## 1. 生命周期状态机（顺序不可跳）

| 阶段 | 做什么 | 负责人 | 产出物（缺一不可） | 门禁 |
|---|---|---|---|---|
| 1 register | 登记：agent 名、负责人、用途、有效期 | twt | `register.md` | 不复用他人凭证；无登记不许评审 |
| 2 review | 评审：用途、权限、风险、回滚路径 | 大管家 | `review.md` | 未评审不许上线 |
| 3 deploy | 上线，走发布门 | 大管家 | `deploy.md` + 发布门三项证据 | 准确/安全/就绪三项证据缺一不许上线 |
| 4 monitor | 运行期健康检查 + 准确率跟踪 + 漂移告警 | twt | `monitor.md` | 健康检查失败不许说“在跑” |
| 5 improve | 改进：改完重走评审 | 撰写席 | `improve.md` | 改进后 review/deploy/monitor 重置重走 |
| 6 retire | 退役 | twt | `retire.md` | 依赖清单须清空；退役后任何推进必拒 |

## 2. 发布门（deploy 前三项证据）

- 准确：黄金任务实测通过记录（`accuracy.md`）。
- 安全：权限清单 + 越权测试通过记录（`safety.md`）。
- 就绪：回滚路径 + 负责人 + 监控已接（`readiness.md`）。
- 三项文件须存在，缺一不许 deploy。

## 3. 降级 / 回滚 / 撤权 / kill switch

- degrade：降级运行（记降级原因与范围），不许静默降级。
- rollback：回滚到上一稳定版（记回滚点）。
- revoke：撤权（撤销该 agent 的凭证与权限）。
- kill：kill switch，一键停用；停用后任何执行必拒，只有重新登记 + 重走评审才能复活。
- 四个动作均须记入 state，事后可查。

## 4. 退役清单（retire 前逐项打勾）

1. 撤销全部凭证（token、密钥、登录态）。
2. 归档日志（运行日志、审计日志、决策记录）。
3. 确认无下游依赖还在调用它（依赖清单为空或已迁移，须有迁移记录）。
4. 通知相关席位（本席、光头、Noesis、大管家、总调度）。

## 5. 门禁脚本用法

```
lifecycle-gate.py init --agent-id <id> --workdir <dir> [--owner <席位>] [--purpose <用途>]
lifecycle-gate.py advance --state <state.json> --stage <阶段> --actor <席位> --artifact <文件>
lifecycle-gate.py degrade --state <state.json> --actor <席位> --reason <原因>
lifecycle-gate.py rollback --state <state.json> --actor <席位> --to <回滚点>
lifecycle-gate.py revoke --state <state.json> --actor <席位> --reason <原因>
lifecycle-gate.py kill --state <state.json> --actor <席位> --reason <原因>
lifecycle-gate.py check --state <state.json>              # 全量校验，违规 exit 1
lifecycle-gate.py status --state <state.json>             # 阶段表
```

- 任何阶段推进前，gate 先验：顺序、产出物存在、发布门证据、依赖清单、kill/retire 状态。
- gate 是唯一放行口径；口头“上线了”不算数，state 文件说了算。

## 6. 违规处理

- gate 拦截即事故信号：停手、报 twt、修状态后再推，不许绕过脚本改 state 文件。
- 同类违规第二次发生，加自动化防护；第三次按系统事故处理。

## 7. 参考

- Microsoft CoE《Manage the agent lifecycle》；AWS 生命周期状态（development→pilot→production→deprecated→decommissioned）；TADL 六阶段五安全门。

## 8. 版本

- v1.0 2026-11-10：首次固化；董事长令 8 类 SOP 全面落地之 4。
