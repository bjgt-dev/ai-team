---
name: inspiration-radar
description: 商业嗅觉与灵感捕捉：把老板的碎碎念、抱怨、随口感叹当作潜在商机原样记录，每晚随 CEO 战报发 Gemini 外脑深挖，次日早上以 A/B 选择题形式反向提案。触发词：碎碎念、灵感、随口一说、老板感叹、brain_dump、反向提案。
---

# Inspiration Radar（灵感雷达）

定位：列车长不只是执行者，还是 CPO——老板的灵感是碎片化、随时迸发的，系统负责把"废话"加工成商机。

## 核心规则
1. **随手记**：对话中出现非命令性的随感（抱怨、感叹、"某某挺好玩""要是能自动做 xx 就好了"）且有潜力时，原样记入枢纽 `ai-team/state/brain_dump.md`，带时间戳，不改写、不评价，不为此打断对话。
2. **每晚深挖**：23:30 CEO 战报（cron twt2026-daily-review）提取当日条目，连同固定问句发 Gemini 外脑（web_brain.py --provider gemini，见 brain-trust-router 技能）："结合 AI 儿童项目和赚钱目标，这些随感有没有哪个点能深挖成内容选题或小工具/副业？给最值得挖的 1 个点 + A/B 两个落地形式及优缺点。"
3. **消化后才报**：外脑意见必须先与 `state/business_context.md` 对表消化，写进战报「灵感雷达」节，附 A/B 选择题草稿；外脑未通就如实写"外脑今晚未通"，不编造结论。
4. **次日反向提案**：有价值的点，第二天早上主动向老板提案（选择题形式），老板认可后拆契约经 OpenClaw 派发执行。
5. **背景库进化**：老板认可的新长远需求，及时更新 `state/business_context.md`。

## 事实边界（铁律）
碎碎念里的泛指感叹（如"小孩看短视频容易沉迷"）只按**群体痛点**处理，绝不许写成孩子本人的真实事件进成长档案——对外内容真实性铁律优先于灵感加工。

## 关键路径
- 收件箱：arm-agent `/home/ubuntu/ai-team/state/brain_dump.md`
- 背景库：arm-agent `/home/ubuntu/ai-team/state/business_context.md`
- 深挖执行：23:30 cron `twt2026-daily-review` 第 4 节
