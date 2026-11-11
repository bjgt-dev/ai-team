# 提示词与配置管理 SOP v1.0（PromptOps，2026-11-10 董事长令，全团队执行）

> 真源：枢纽 `~/ai-team/docs/sops/promptops-sop.md`；本地 `~/workspace/sops/promptops-sop.md` 只作镜像。
> 机制：`~/ai-team/scripts/promptops-gate.py`（门禁脚本，本地镜像 `~/workspace/tools/promptops-gate.py`）。
> 原则：提示词当代码管；文档不是机制——违反本 SOP 的推进会被 gate 脚本拦截。

## 0. 适用范围

- 本席、光头、Noesis、大管家、总调度、工人、复核席，全部执行。
- 任何提示词、系统提示、配置、模型选型的变更一律走本 SOP；SOUL/MEMORY/六件套同步同理。

## 1. 五步链（顺序不可跳）

| 阶段 | 做什么 | 负责人 | 产出物（缺一不可） | 门禁 |
|---|---|---|---|---|
| 1 version | 新版：语义版本号 + 变更说明 | 撰写席 | `prompt-vX.Y.Z.md` | 版本号须合规（MAJOR 破坏性 / MINOR 改进 / PATCH 修辞） |
| 2 review | PR 评审：评审人签字 | 大管家 | `review.md` | 未评审不许进 CI |
| 3 ci | CI 跑黄金集 eval | 检查席 | `ci-result.md` | 黄金集文件须存在，CI 须全部通过 |
| 4 canary | 灰度 1%→10%→50%→100% | twt | 每档 `canary-<档>.md` | 逐档推进，每档须有观测记录，不许跳档 |
| 5 production | production 标签指向新版 | twt | `production.md`（提示词版本 + 模型版本） | 标签须同时钉死提示词版本与模型版本，缺一不许发布 |

## 2. 语义版本号

- MAJOR：破坏性变更（改目标、改输出格式、改角色边界），须董事长知晓。
- MINOR：改进（加能力、改措辞、补边界），须大管家评审。
- PATCH：修辞（错别字、格式、标点），可由 twt 直接批。
- 版本号一经发布不可变；改了就是新版。

## 3. 灰度档位

- 1% → 10% → 50% → 100%，每档至少留一次观测记录（错误率、退化信号、用户反馈）。
- 任一档出现退化信号立即停灰度、走回滚，不许硬推下一档。
- 影子模式（只记分、不生效）可作 0% 档，先行。

## 4. 回滚

- 回滚 = 移动 production 标签：`rollback` 命令把 production 标签指回上一稳定版，秒级，不用重新部署。
- 回滚后须记回滚原因，并把触发回滚的失败变成回归用例（见评测验收 SOP）。

## 5. 钉死版本

- production 标签须同时记录提示词版本 + 模型版本；上线时两者一起钉死，不许只钉一个。
- 模型换了、提示词没换，或反之，都是新版，都要重走五步链。

## 6. 门禁脚本用法

```
promptops-gate.py init --prompt-id <id> --workdir <dir> [--model <模型版本>]
promptops-gate.py advance --state <state.json> --stage <阶段> --actor <席位> --artifact <文件> [--version <X.Y.Z>] [--tier <1|10|50|100>]
promptops-gate.py rollback --state <state.json> --actor <席位> --to-version <X.Y.Z> --reason <原因>
promptops-gate.py check --state <state.json>              # 全量校验，违规 exit 1
promptops-gate.py status --state <state.json>             # 阶段表
```

- 任何阶段推进前，gate 先验：顺序、版本号合规、评审记录存在、CI 全过、灰度逐档有观测记录、production 标签双版本齐全。
- gate 是唯一放行口径；口头“已上线”不算数，state 文件说了算。

## 7. 违规处理

- gate 拦截即事故信号：停手、报 twt、修状态后再推，不许绕过脚本改 state 文件。
- 同类违规第二次发生，加自动化防护；第三次按系统事故处理。

## 8. 参考

- Microsoft《实现提示版本控制与优化》；LangSmith / PromptLayer / Langfuse 的 registry 模式；中文社区 PromptOps 文章。

## 9. 版本

- v1.0 2026-11-10：首次固化；董事长令 8 类 SOP 全面落地之 5。
