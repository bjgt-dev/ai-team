---
name: local-intel-harness
description: "跑一轮情报 3 小时增量扫并完成分流复核与登记：采集走枢纽生产管线（不动它），Harness 只做编排、复核与验收。触发场景：每 3 小时一轮增量扫到期 / 手动补扫一轮。产出：runs/<时间>/report.md（逐条 DONE / BLOCKED / 待核结论）+ 增量记录与技能登记表的更新。不做：不改生产脚本、不直接加网关渠道（那是 local-free-channel-harness 的事）、不替用户决定领不领某个额度。"
---

# 情报增量扫 Harness

## 背景原则

采集早就脚本化了（枢纽 `intel_pipeline.py --incremental`），手搓的不是采集，是**每轮之后没人负责的三件事**：
没人核对免费命中是真是假、没人核对技能登记写没写对、没人把源失败说出来。这个 Harness 只包这三件事，
采集本身一个字不动。

## 边界（先判断要不要进这个 Harness）

- 进：定时增量轮到期、手动补扫一轮、上一轮有 BLOCKED 要重跑。
- 不进：只想看今天简报（直接读 `state/intel_latest.md`）；要改采集逻辑（那是改生产脚本，先报父级）；
  要加网关渠道（走免费渠道 Harness 的 SOP）。

## 工作流总览

```text
Phase 0  Intake        确认本轮该跑 + 上一轮无未结 BLOCKED（有则先处理）
   ▼
Phase 1  Collect       scripts/run-incremental.sh 跑生产管线（带锁、带过载保护）
         └ 取证：登记表行数 / 当日增量记录行数 / 报警文件（跑前跑后各一份）
   ▼
★ Gate A  采集门：result.json 有效且 mode=incremental；过载跳过判 DEFERRED，不算失败
   ▼
Phase 2  Triage        按 references/triage-guide.md 逐条复核免费命中与技能命中
         └ 每条定性（SOP / 已有 / 非模型 / 假命中；登记 / 待验 / 不登），写进 report.md
   ▼
Phase 3  Verify        scripts/check-run.py 机器验收（见验收表）
   ▼
★ Gate B  验收门：check.json verdict= PASS 才进 Phase 4；FAIL 停住，修 Harness 不修生产
   ▼
Phase 4  Register      核对登记：技能已确认项确已进登记表第七节；SOP 项只记待办，不直接入库
   ▼
Phase 5  Report        写 runs/<RUN_ID>/report.md：逐条结论 + 源失败清单 + 下一步
```

## 状态文件（长期记忆，不依赖聊天上下文）

```text
~/workspace/custom-skills/local-intel-harness/
  config.env                 # 外置配置：枢纽地址、脚本路径、state 路径（改这里，不改脚本）
  SKILL.md                   # 本文件
  scripts/
    run-incremental.sh       # Phase 1：跑生产管线 + 跑前跑后取证
    check-run.py             # Phase 3：机器验收，写 check.json，退出码判定
  references/
    triage-guide.md          # Phase 2：免费命中与技能命中的复核口径
  runs/<YYYYMMDD-HHMM>/
    run-id.txt  result.json  collect.err  meta.json  record-tail.txt
    check.json               # Phase 3 产物
    report.md                # Phase 5 产物：本轮唯一对外的结论
```

生产侧状态（枢纽，只读不写——除生产脚本自己写的）：

```text
~/ai-team/state/intel-incremental-<日期>.md   # 当日增量记录
~/ai-team/state/intel-seen.json              # 去重状态（7 天过期）
~/ai-team/state/skills-registry.md           # 技能登记表（第七节为增量扫自动追加）
~/ai-team/state/outbox/intel-new-free-*.md   # 免费命中报警（有命中才有文件）
```

## 硬性质检协议（多层审核，不只“跑通了就行”）

每轮开五层（详见 `references/review-layers.md`，逐项打勾）：

| 层 | 审什么 | 审者 | 产物 |
|---|---|---|---|
| **L1 结构层** | 产物形状对不对：JSON 有效、7 个源齐全、四数非负、记录增长、登记行数 = 上报数、报警文件数 = 命中数 | `check-run.py` 脚本 | `check.json` |
| **L2 事实层** | 每个数字追不追得到源：报告里的数都能在 `result.json` / `meta.json` / `record-tail.txt` 里找到；没证据不写完成；不确定的写“未核实” | 主 Agent 内联 | `report.md` 里的数字 |
| **L3 内容层** | 每条新内容有没有去向：免费命中四档、技能命中三档，无遗漏；SOP 项只记待办不直接加网关；边界守住 | 主 Agent 内联（按 `references/triage-guide.md`） | `report.md` 里的逐条结论 |
| **L4 风格层** | 结论先行、≤300 字、脱名、只报结论/阻塞/下一步 | 主 Agent 内联（写报告前打勾） | `report.md` |
| **L5 可执行层** | 跑不跑得通：脚本退出码、验收全绿、重跑幂等、回滚能用（备份在） | 脚本 | `check.json` verdict |

终审：常规轮次不设独立复核；每周抽查 5% 的轮次，由未参与本轮的 Agent 按
`references/review-layers.md` 重验一遍，结论写进该轮 `report.md` 末尾。

铁律：

1. **Gate A / Gate B 是硬停**：不通过不进下一 Phase；过载 DEFERRED 不算失败，下一轮再跑。
2. **源失败必须显式列出**：Linux Do 403、X 缺 cookie、GitHub 限流，写进 `check.json` 与 `report.md`，
   不许静默跳过、不许拿旧结果冒充新结果。
3. **SOP 项只记待办**：复核定性为 SOP 的免费线索，**不直接加网关**——加网关是另一个 Harness 的事，
   本轮只在报告里列出待办清单。
4. **修 Harness 不修生产**：验收 FAIL 先查 Harness 自己的脚本与取证，生产脚本有问题先报父级，
   不要顺手改生产脚本。
5. **最小切片修复**：失败只修失败的那一项，修完只重审失败的层；同一层连续两次 FAIL 停下报父级；
   有修复写 `repair-log.md`（本轮修了什么见 runs 报告）。

## 验收表（check-run.py 逐项判定）

| # | 检查项 | 通过条件 |
|---|---|---|
| 1 | result_json_valid | `result.json` 能解析（锁 SKIP 导致空输出时 FAIL） |
| 2 | mode_incremental | `mode == "incremental"`（过载时判 DEFERRED） |
| 3 | source_skills_present | `sources` 含 `技能` |
| 4 | counts_nonnegative | `new >= 0` 且 `skill_new >= 0` |
| 5 | record_grew | `new > 0` 时增量记录行数增长；`new == 0` 时不增长为正常 |
| 6 | record_has_round_section | `new > 0` 时记录末尾有本轮 `## HH:MM 增量` 小节 |
| 7 | skills_appended_matches_registry | `skills_appended == 登记表第七节实际增加行数` |
| 8 | free_hits_matches_alert | `free_hits > 0` 当且仅当当日有报警文件 |
| 9 | source_failures_listed | 源失败逐条列出（0 项时记“无”） |

## 成功标准

- 一轮跑完有 `report.md`，逐条结论齐全，无“没看”项。
- `check.json` verdict = PASS（或 DEFERRED 且原因写清）。
- 登记表第七节行数与 `skills_appended` 对得上；报警文件与 `free_hits` 对得上。
- 下一轮不依赖上一轮的聊天上下文：只读 `runs/` 与枢纽 state 就能续跑。

## 相关资源

| 文件 | 何时读 |
|---|---|
| `references/triage-guide.md` | Phase 2 每次 |
| `references/review-layers.md` | 多层审核：L1–L5 与抽查的逐项清单（每轮打勾） |
| `config.env` | 改枢纽地址/路径时 |
| `../local-harness-method/SKILL.md` | 想改 Harness 结构时（先读方法论） |
