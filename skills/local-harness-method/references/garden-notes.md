# garden-skills 结构要点（对照源，2026-11-06 读）

研究对象：`https://github.com/ConardLi/garden-skills`（只读 clone 到 /tmp，未改动）。
视频：`https://youtu.be/hbgVc1Oixyo`《Harness 实践：将任何文字编辑成精美的文章》——
核心论点：好的 Harness 是可以迁移的；beautiful-article 就是证明。

## 每个技能的固定骨架

```text
skills/<name>/
  SKILL.md            # 契约：frontmatter（name + description，含触发场景与边界）+ 工作流
  manifest.json       # name / version / category / description / homepage / compat[]
  README.md           # 对人讲的介绍
  scripts/            # 确定性可执行物：scaffold.sh、转换脚本、渲染脚本、验收脚本、check-env
  references/         # 渐进加载的知识：每个文件只在对应 Phase 读
  assets/             # 脚手架模板、编辑器、起始项目
  theme-profiles/ 或 styles/ 或 style-recipes/   # 可选配置库：主题/风格/配方都是数据文件，不是代码
```

`manifest.json` 的 `compat` 是**声明**，不是愿望：
`["claude-code", "claude-ai", "cursor", "codex-cli", "gemini-cli", "opencode"]`——
能进这个列表，是因为技能只依赖通用能力（文件 + shell + 指令遵循），不是因为它在六家上都测过。

## 三个技能各取了什么

### beautiful-article（最完整的 Harness 样本）

- 六问见 `references/harness.md`：上下文、工具、编排、状态与记忆、评估与观测、约束与恢复。
- 工作流 9 个 Phase（0 Intake → 8 Delivery），3 个 ★硬检查点（Plan / First Spread / Final），
  每个检查点都有“决策收集铁律”：每项独立列出、独立等答复、可推荐、不可静默默认、不可打包成一个 yes/no。
- 状态文件是长期记忆：`source/source.md`、`plan/plan.md`（Brief/Outline/Theme/Assets 四段合一）、
  `review/first-spread-review.md`、`review/final-review.md`；只在需要时才写 `source-review.md` / `repair-log.md`。
- 质检按节点区分（本方法论规 5 的来源）：Plan 内联自查禁开 SubAgent；First Spread / Final 用
  SubAgent + 写文件；Section 用 SubAgent + 消息返回 pass/fail，不写文件。
- `scripts/scaffold.sh`：一键建工作区（校验主题、拷模板、注主题、npm install、typecheck），
  跑完即告诉用户下一步读什么。
- 一节一文件铁律：`article/sections/NN-*.tsx`，assembler 只做 import + 排序——文件级隔离是多 Agent 并行的前提。

### web-design-engineer

- Step 0 先验事实再动手：不确定的产品/版本/规格先查权威来源，查不到就问用户，禁猜。
- 五维 Design Read（visual-variance / motion-intensity / information-density /
  asset-dependence / brand-fidelity）：先定调再选 token，调子是决策变量不是装饰分。
- 风格配方是数据：`references/style-recipes/*.md` 25 个锚定配方（Linear / Aesop / Stripe Press…），
  每个含配色、字体、招式、反模式；想加风格就加文件，不改 SKILL.md。
- 验收分两层：轻量自检清单（默认不启动浏览器）+ 可执行浏览器验收（仅当用户明确要求时），
  后者走 `references/browser-acceptance.md`，证据先行、修好再交付。

### motion-showreel

- 可迁移的核心是一个**确定性契约**：页面暴露 `render(t)`，画面只由 t 决定（禁 Math.random / Date.now /
  rAF / CSS 动画），脚本逐帧截图再用 ffmpeg 合成；确定性检查 `render.mjs --determinism` 不通过不准导出。
- 时间轴是单一事实源：`timeline.js` 里写 `SHOTS` / `HOLDS` / `DROPS`，页面、配乐、验收工具都读它。
- 验收是程序判定：`inspect.mjs qa` 检查解码、尺寸、帧率、时长、帧数、音轨、黑帧、响度、真峰值、
  循环接缝，一项 FAIL 退出码即 1；没传的规格列 `NOT CHECKED`，不算通过。
- 审片是给人判断的：抽帧、切点、运动、文字框景，逐项写观察 + 最严重的 3 个问题；修完只重渲有问题的段。
- 脚手架带自测：`scripts/test/*.test.mjs`，`cd scripts && npm test`。

## 迁移性的根

1. **契约在 SKILL.md，执行在脚本，知识在 references，配置在数据文件**——四者分离，各自可换。
2. **关键路径只用通用能力**；厂商私有能力只作“优先 + 回退”，两种路径都写全。
3. **状态落盘**，不依赖聊天上下文；长会话回读文件，不凭记忆重发明。
4. **验收是程序**：退出码 + 数字 + 文件，不是 Agent 自评。
5. **检查点是硬停**：用户确认型逐项等答复；无人值守型用验证门，不通过就停，不静默跳过。
