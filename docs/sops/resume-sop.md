# 简历撰写 SOP v1.0（2026-11-10 董事长令，全团队执行）

> 真源：枢纽 `~/ai-team/docs/sops/resume-sop.md`；本地 `~/workspace/sops/resume-sop.md` 只作镜像。
> 机制：`~/ai-team/scripts/resume-sop-gate.py`（门禁脚本，本地镜像 `~/workspace/tools/resume-sop-gate.py`）。
> 原则：分模块、分步骤、多人分工；文档不是机制——违反本 SOP 的推进会被 gate 脚本拦截。

## 0. 适用范围

- 本席、光头、Noesis、大管家、总调度、工人、复核席，任何人经手简历一律走本 SOP。
- 简历指董事长本人求职简历；一岗一版，每版独立建 job。

## 1. 七步 + 验收（顺序不可跳）

| 阶段 | 做什么 | 负责人 | 产出物（缺一不可） | 门禁 |
|---|---|---|---|---|
| 1 intake | 收齐目标岗位、JD、简历源文件 | twt | `jd/`（3–5 个 JD）、`resume-source/` | 无源文件不许开工 |
| 2 jd_analysis | 读 JD，抽关键词清单 | 分析席 | `keywords.md`（必须项/加分项/行业术语） | 无清单不许搭框架 |
| 3 material | 素材库：全部经历、项目、证书，STAR 记每段，成果先找数字 | 撰写席 | `material.md` | 无数字不许写句子 |
| 4 framework | 版式、模块顺序、每模块放什么，定死 | 大管家定案，董事长拍板 | `framework.md` + frozen 标记 | 框架不定稿、不冻结，不许写文 |
| 5 drafting | 分模块撰写：经历、摘要、技能分开写 | 撰写席 | `draft.md` | 撰写席 ≠ 审核席 |
| 6 tailor | 一岗一版：按目标 JD 调侧重点 | 撰写席 | `resume-<岗位>.md` | 一稿不许多投 |
| 7 ats | ATS 检查：关键词覆盖率、标准标题、不用表格/文本框、PDF 文本可选 | 检查席 | `ats-check.md` | 不过不许进审核 |
| 8 review | 四轮审核，每轮换人 | 见 §2 | 每轮一份 `review-<轮>.md` | 四轮四人，缺一轮不许验收 |
| 9 acceptance | 董事长验收 | 董事长 | 验收签字（state 文件） | 只有董事长能签 |

## 2. 四轮审核（每轮换人，换视角）

1. **内容轮**：与目标岗位匹配吗？无关经历删了吗？
2. **事实轮**：日期、抬头、公司名、数字逐项对源核对。
3. **语言轮**：错别字、时态、标点、动词强度。
4. **排版轮**：分页、间距、对齐、导出后换设备看一遍。

- 四轮审核人互不相同，且四人均不得是撰写席。
- 同一轮发现的问题先记进 `feedback.md`，不许当场改。

## 3. 意见批量制（2026-11-09 董事长令，硬规）

- 董事长、大管家、审核人指出问题时，只记录、不动手。
- `feedback_mode=collecting` 期间，`drafting`/`tailor` 阶段禁止推进（gate 拦截）。
- 董事长说“改”后，`feedback_mode=approved`，一次改完、一次出新版。
- 指出问题 ≠ 让改；问句 ≠ 改令。

## 4. 分工铁律

- 不是一个人干所有事：撰写、审核、验收三权分立；同一 job 里撰写席不得兼任任何一轮审核。
- 每阶段 state 文件记 actor（席位名），gate 校验分工；违规推进 exit 1。

## 5. 门禁脚本用法

```
resume-sop-gate.py init --job-id <id> --workdir <dir>
resume-sop-gate.py advance --state <state.json> --stage <阶段> --actor <席位> --artifact <文件> [--frozen]
resume-sop-gate.py collect-feedback --state <state.json>   # 进入批量收意见模式
resume-sop-gate.py approve-edit --state <state.json>       # 董事长说“改”后放行
resume-sop-gate.py check --state <state.json>              # 全量校验，违规 exit 1
resume-sop-gate.py status --state <state.json>             # 阶段表
```

- 任何阶段推进前，gate 先验：顺序、产出物存在、框架已冻结、分工、意见模式。
- gate 是唯一放行口径；口头“好了”不算数，state 文件说了算。

## 6. 违规处理

- gate 拦截即事故信号：停手、报 twt、修状态后再推，不许绕过脚本改 state 文件。
- 同类违规第二次发生，加自动化防护；第三次按系统事故处理。

## 8. 写作流程（收集→结构→填充→定制→润色→检查→复核→交付）

写文章（简历正文）按以下八步走，顺序不可跳；每步的产出物就是下一步的输入。依据：resume.io、TopResume、ResumeGemini、Resumeble、Harvard 职业中心五家收敛 + GitHub career-ops（77.4K★）skill 文件思路；董事长五步为骨架，业内共识补三步（收集、定制、复核）。

1. **收集**（对 stage 1 intake + stage 3 material）：收齐目标岗位、JD、全部经历与数字，再动手。没素材不许搭结构。
2. **结构**（对 stage 4 framework）：先定版式、模块顺序、每模块放什么，冻结后再写。没框架不许写正文。
3. **填充**（对 stage 5 drafting）：按模块填内容，经历用 STAR、成果先找数字；子弹句 = 动词 + 做了什么 + 结果。
4. **定制**（对 stage 6 tailor）：一岗一版，按目标 JD 调侧重点、注入关键词、删无关经历；只调内容，不改结构。
5. **润色**（对 stage 5 drafting 内 + stage 8 review 前）：语言打磨——动词换强的、句式收紧、去冗余、统一口径与数字写法；润色不改事实、不改结构。
6. **检查**（对 stage 8 review 语言轮）：错别字、标点、时态、数字与日期格式逐项过一遍；自己查完才能进复核。
7. **复核**（对 stage 8 review 四轮）：换人审——内容轮、事实轮、语言轮、排版轮各换一人，四人均不得是撰写席；问题先记 feedback.md，不许当场改。
8. **交付**（对 stage 9 acceptance）：董事长验收签字后才算交付；未验收的版本不许对外投递。

- 润色、检查、复核是三步，不许合并：润色改表达，检查抓错误，复核换视角。
- 框架冻结后，填充、定制、润色不得改结构；要改结构须回 stage 4 重新冻结。

## 9. 版本

- v1.0 2026-11-10：市面共识（resume.io 六步法、useresume.ai 八步法、TopResume 五步法）+ 团队分工映射；董事长令固化成机制。
- v1.1 2026-11-12：补 §8 写作流程（董事长令“去搜集”后实查：五家业内最佳实践收敛 + GitHub Reactive Resume 45.5K★、career-ops 77.4K★；董事长五步为骨架，补收集、定制、复核三步，共八步，映射到 stage 1/3/4/5/6/8/9）。
