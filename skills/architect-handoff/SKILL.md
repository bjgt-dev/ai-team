---
name: architect-handoff
description: 无缝交接与切号机制：列车长可替换、发动机不换。所有业务记忆落盘 VPS state/，标准接管 Prompt（conductor_prompt.md）让新账号一句话接管全盘。触发词：交接、切号、接管、换账号、handoff、boot、save_state。
---

# Architect Handoff（无缝交接与切号）

## 架构铁律
- **列车长（前端，可替换）**：Twt2026 / Noesis / 光头走天下轮值，负责"想和说"——理解意图、补全需求、调用外脑、产出方案与契约参数。
- **发动机（OpenClaw 网关，不换）**：统一生成 JSON 契约写入 contracts/pending/、调度 Worker、verify.py 门禁验收。
- 列车长绝不绕过 OpenClaw 直接调 Worker；发动机抢修期可临时兜底，修好即交还并写进当日战报。

## 落盘纪律（私人物品清零）
业务状态只许存在于枢纽 `ai-team/state/`，不许只藏在某个账号的聊天记录里：
- `context.md` 当前主线与基建状态（有变化即更新）
- `pending.md` 待办：待拍板 / 进行中 / 长期待办
- `handoff.md` 交接流水（只追加，写清本次会话做了什么、卡在哪）
- `quota.md` 账号额度账本；`workers.md` 工人状态
- `brain_dump.md` 灵感收件箱；`business_context.md` 业务背景库
- `MUSE-SWITCH.md` 三账号切换 SOP（触发阈值：剩余额度 10%）
离任/切号前必跑：更新 context + pending，handoff 追加一条，quota 记一行（用 `scripts/save_state.sh`，四参数强制）。

## 接管流程（新列车长第一课）
1. `ssh arm-agent` 后运行 `/home/ubuntu/ai-team/scripts/boot.sh`。
2. 依次读 state 六件套：MUSE-SWITCH、context、handoff、pending、business_context、brain_dump。
3. 向老板汇报三件事：当前主线、待派/在跑任务、待拍板事项。汇报完等指令，不自作主张开新线。
标准接管 Prompt 在 `state/conductor_prompt.md`——老板把全文粘贴给新账号，再发一句"按接管动作执行"即可完成交接。conductor_prompt 内容有变（规则升级）时，先更新枢纽版，再同步列车长本地副本。

## 验收标准
新账号读完六件套后汇报的内容，应与离任者能报出的完全一致（主线/任务/待拍板三对齐）。对不齐就是落盘纪律没执行到位，先补盘再交接。

## 关键路径
- 状态中枢：arm-agent `/home/ubuntu/ai-team/state/`
- 脚本：`scripts/boot.sh`、`scripts/save_state.sh`
- 切换 SOP：`state/MUSE-SWITCH.md`；接管 Prompt：`state/conductor_prompt.md`
