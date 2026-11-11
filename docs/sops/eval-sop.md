# 评测验收 SOP v1.0（2026-11-10 董事长令，全团队执行）

> 真源：枢纽 `~/ai-team/docs/sops/eval-sop.md`；本地 `~/workspace/sops/eval-sop.md` 只作镜像。
> 机制：`~/ai-team/scripts/eval-gate.py`（门禁脚本，本地镜像 `~/workspace/tools/eval-gate.py`）。
> 原则：没有评测集就没有发布；没有基线对比就没有“变好了”；文档不是机制——违反本 SOP 的推进会被 gate 脚本拦截。
> 参考：Anthropic《Demystifying evals for AI agents》、三层评测模型（单元/集成/回归）、promptfoo（27,000★，只借鉴思路，不照搬工具）。

## 0. 适用范围

- 本席、光头、Noesis、大管家、总调度、工人、复核席，任何人改提示词、模型、工作流，或发布任何 AI 产出，一律走本 SOP。
- 评测对象：提示词、模型、Agent 工作流、以及一切要发布的 AI 产出。

## 1. 六步 + 发布三档（顺序不可跳）

| 阶段 | 做什么 | 负责人 | 产出物（缺一不可） | 门禁 |
|---|---|---|---|---|
| 1 evalset | 建固定评测集：任务 + 预期结果 + 评分规则 | 评测席 | `evalset.json`（带版本号，见 §2） | 无评测集不许跑回归 |
| 2 baseline | 在改动前跑一遍评测集，记基线 | 评测席 | `baseline.json`（逐维度分数 + 评测集版本） | 基线版本须与评测集一致 |
| 3 change | 改提示词 / 模型 / 工作流，写清改了什么 | 改动席 | `change.md` + 改动类型 | 不声明改动类型不许推进 |
| 4 regression | 改动后重跑同一评测集 | 评测席 | `regression.json`（逐维度分数） | 评分器未校准不许跑（见 §3） |
| 5 compare | 逐维度对比回归与基线 | 评测席 | `compare.md`（逐维度对比记录） | 任一维度退化即拦截发布 |
| 6 publish | 分三档发布：smoke → regression → full | 发布席 | 每档一份发布记录 | 档位不可跳，见 §5 |

## 2. 固定评测集（铁律）

- 评测集文件须存在且带版本号（`version` 字段，如 `v1.0`）；无版本、任务为空、任务缺预期结果，一律不许开工。
- 每个任务必须写明三件事：任务本身、预期结果、评分规则。
- 评分规则只分三类，缺一不可按类标清：
  1. **代码断言（assertion）**：机器可验的二值条件（如输出含某字段、格式合法、调用成功），优先用；
  2. **LLM-as-judge**：用模型打分，必须先校准（见 §3），且打分标准写进评测集；
  3. **人工（human）**：人工判断，必须留判断记录，判断人不得是改动席。
- 验收标准写成机器可验的二值条件：每条标准只有“通过 / 不通过”，不写“效果不错”“基本可用”这类无法机器复核的词。
- 三层评测模型：单元（单个提示词/工具调用）→ 集成（多步工作流端到端）→ 回归（固定集全量重跑）；改动影响哪层就测哪层，回归层每次必跑。

## 3. 评分器先校准（铁律）

- LLM-as-judge 与人工评分器，在跑回归之前必须先对人工判断校准：抽样对照、记一致率，校准记录文件须存在才许跑回归。
- 校准记录须写明：抽样条数、对照方式、一致率、校准人、校准时间；一致率不达标（低于约定阈值）须先修评分标准，不许带着未校准的评分器跑回归。
- gate 校验：`regression` 阶段推进前，校准记录文件必须存在，否则 exit 1。

## 4. 改动必跑回归、与基线对比（铁律）

- 改提示词、改模型、改工作流，三者任何一项改动，必跑回归集并与基线逐维度对比；基线文件须存在，回归结果须逐维度记录（`dimensions` 字段，维度名 → 分数）。
- 对比维度缺失、回归文件缺维度，视为退化处理，拦截发布。
- 任一维度退化不许发布：回归任一维度分数低于基线，`compare` 与 `publish` 一律拦截；想发布先修改动、重跑回归，直到全部维度不低于基线。

## 5. 发布流程 smoke → regression → full（铁律）

- 发布分三档，顺序不可跳：先 smoke（小范围冒烟），再 regression（回归集全量验证通过后扩大），最后 full（全量）。
- 不许从 smoke 直接跳 full，不许跳过 regression 直发 full；gate 按档位顺序校验。
- 每档发布前重验：回归无退化 + 无待登记的生产失败（见 §6），否则拦截。

## 6. 生产失败变新回归用例（铁律）

- 生产上出的任何失败，须先 `record-failure` 登记为待登记案例，再写进评测集文件（新增任务，预期结果写明“不再复现”），然后 `register-failure` 确认登记。
- 失败案例须登记进评测集才许关单、才许发布：有待登记失败时，`publish` 一律拦截。
- 未写进评测集文件就声称已登记，gate 核对文件内容后拦截。

## 7. 门禁脚本用法

```
eval-gate.py init --job-id <id> --workdir <dir>
eval-gate.py advance --state <state.json> --stage <阶段> --actor <席位> --artifact <文件>
                     [--change-type prompt|model|workflow] [--tier smoke|regression|full]
eval-gate.py calibrate --state <state.json> --artifact <校准记录文件>
eval-gate.py record-failure --state <state.json> --case-id <id>      # 生产失败先登记
eval-gate.py register-failure --state <state.json> --case-id <id>    # 已写进评测集后确认
eval-gate.py check --state <state.json>              # 全量校验，违规 exit 1
eval-gate.py status --state <state.json>             # 阶段表
```

- 任何阶段推进前，gate 先验：顺序、产出物存在、评测集合法带版本、基线版本一致、改动类型、校准记录、逐维度不退化、失败已登记、发布档位顺序。
- gate 是唯一放行口径；口头“测过了”“没退化”不算数，state 文件 + 评测集文件 + 分数文件说了算。

## 8. 违规处理

- gate 拦截即事故信号：停手、报 twt、修状态后再推，不许绕过脚本改 state 文件；手工篡改 state 会被 `check` 全量校验抓获。
- 同类违规第二次发生，加自动化防护；第三次按系统事故处理。

## 9. 版本

- v1.0 2026-11-10：Anthropic evals 方法论 + 三层评测模型 + promptfoo 思路 + 团队发布流程固化成机制。
