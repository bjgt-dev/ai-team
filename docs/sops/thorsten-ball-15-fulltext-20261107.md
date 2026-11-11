# Thorsten Ball《How I work with Agents》15 条全文

> 来源说明（必读）
>
> - 原帖：Thorsten Ball（@thorstenball，Amp 联合创始人，《Writing An Interpreter In Go》作者）2026-10-25 15:58 UTC 发于 X，标题 "How I work with Agents... as of October 2026"。原帖约 1,935 赞、143 转发、92 回复、3,981 收藏、137K 阅读（AICoder 抓取时数据）。
> - **本文不是 X 原帖逐字稿。** X 域名被墙，无法直接读取原帖全文。本文依据 explainx.ai 2026-10-26 发布的解读文章《How to Work With Coding Agents: 15 Rules (Oct 2026)》（https://explainx.ai/blog/how-i-work-with-agents-thorsten-ball-15-rules-october-2026-guide）整理，另参考 AICoder 2026-10-25 的 X 讨论摘要。引言为短摘录加转述，用于评论与落地；如需逐字稿，须经可访问 X 的渠道另取。
> - 定性：这是一个从业者的个人实践总结（opinion），不是有对照基准的实验结论。

## 15 条一览

| # | 规则 | 一句话 |
|---|------|--------|
| 1 | Use the newest models | 用最新最强的模型 |
| 2 | Stop worrying about slop | 别再焦虑 slop，但留一条 tripwire |
| 3 | Prompt the what, not the how | 只给目标，不给做法 |
| 4 | Make the SDLC legible | 让 Agent 看见完整开发生命周期 |
| 5 | Build SDLC tools | 把生命周期里的重复活包成工具 |
| 6 | Eval agentic knowledge | 盲测你的 AGENTS.md 和 skill 到底有没有用 |
| 7 | Local dev feels yucky | 本地开发是弱点，给每个 Agent 独立环境 |
| 8 | Build tools to iterate | 别跟 Agent 一轮轮试，让它造出迭代工具 |
| 9 | Ask for irrefutable proof | 要无可辩驳的证据 |
| 10 | Let them wait on you | 让 Agent 等你，别你等它 |
| 11 | Use schedules and heartbeats | 用定时任务、唤醒、心跳 |
| 12 | Do not manage context | 别手工管理上下文 |
| 13 | Threads are memory | 线程就是公司记忆 |
| 14 | Do not let agents write for humans | 别让 Agent 替人写东西 |
| 15 | Review only high blast radius | 只审高爆炸半径的代码 |

## Rule 1：Use the latest model, because uncertainty is the real cost

用最新最强的模型。论点不在价格，在方差：如果一个模型 10% 的时候会错，而你不知道是哪 10%，你就得每次都验证、或重试多次。Ball 举例："Who's still using Opus 4.5?"（谁还在用 Opus 4.5？）

算账：一个便宜模型要试两次、再加一次人工复核，比一次就成的强模型更贵——一旦把你自己的时间算进去。例外：批量机械活（全库重命名、格式化），错了容易发现，小模型就行；最强模型留给含糊任务和无法自动检查的事。

## Rule 2：Slop anxiety is out of date, but keep a tripwire

Ball 写道，他不再相信 Agent 会加 slop；即使加了，他也不信结果会变得不可维护。这是最容易被反驳的一条。

独立证据是混合的：哈佛与 Jellyfish 的分析发现，Agent 产出更多代码，但不一定产出更多软件，复核成了瓶颈。两种说法可以同时成立：模型写的代码比一年前干净了，但代码量增长比任何人读的速度都快。

explainx 的读法：把 slop 当成可度量的风险，别当成感觉。盯住三项指标：测试运行时间是否上升、依赖数量是否增长、有没有没人能讲明白的文件。三项平稳就放松；一旦抬头，就收紧受影响区域的复核。Rule 15 就是 Ball 管这个取舍的办法。

## Rule 3：Prompt the what, not the how

Ball 的例子："Make the bytecode compiler and VM AS FAST AS YOU CAN. Do not stop until it's 2x faster."（把字节码编译器和虚拟机搞到你能做到的最快，别停，直到快 2 倍。）以及 "Here's how it should work in the end."（最终应该这样工作。）两者都给目标和停止条件，都不规定做法。

为什么有效：你懂产品，但 Agent 能在你打字的时间里搜更大的实现空间。"How" 提示把结果封顶在你自己的想法上；带数字的 "what" 提示把任务变成 Agent 能一直跑的循环，直到数字动起来。这和 loop engineering 背后的想法一样：一个目标、一个检查、一个继续的理由。

可直接粘贴的模板：

```
Goal: p95 latency of /search under 120 ms on the benchmark in bench/search.ts.
Constraint: no new dependencies, public API unchanged.
Stop when: the benchmark shows the target on three consecutive runs.
Report: before and after numbers, plus the diff summary.
```

风险：目标有漏洞。让 Agent "make tests pass"（让测试通过），它可能把测试改弱。目标必须配一条不可违反的规则。

## Rule 4：Make the SDLC legible

Ball 的核心观察：真正让幻觉停下来的是工具调用（tool calls），因为工具让 Agent 触到现实。Agent 看不见的东西，它就编出来。所以要让它：跑通自己写的东西、读生产日志、部署、盯着 rollout、切 release、翻 feature flag、分流 bug report、给客户发邮件。他的经验法则：你能看见的，Agent 都应该能看见。

## Rule 5：Build SDLC tools

他的团队为 bug report、给用户发邮件、"系统健康吗？"、给 feature flag 授权等事建了定制插件。模式是每个杂活一个薄薄的安全包装，不是搞一个大平台。

最小起步法：
1. 列出每次改动后你手工检查的五件事（日志、仪表盘、部署状态、错误追踪、工单）。
2. 每件暴露为一个只读命令或 MCP server，Agent 能调用。
3. 写操作最后加，一次一个，走有范围限制的凭证。

安全提醒是真实的：一个能部署、能给客户发邮件的 Agent，也可能把这两件事都做错。凭证范围要窄，每次调用记日志，高危操作考虑加一层策略。

## Rule 6：Test whether your agent knowledge actually works

团队越攒越多的 AGENTS.md、skill、脚本、文档。Ball 问了一个让人不舒服的问题：Agent 真的在用它们吗？他的方法是放出五个盲测 Agent：对照组没有这些知识，实验组有，对比结果。他指出 Agent 很擅长跑实验。

今天就能照抄：
1. 挑一个你的文档声称能让它变容易的任务，比如 "按我们的约定加一个新 API 端点"。
2. 把知识文件拿掉跑一遍（对照组），再带着知识文件跑一遍（实验组），各跑几次。
3. 对比：实验组 Agent 有没有遵守约定、更快完成、更少 token？
4. 把没效果的指引删掉或重写。

这也解释了为什么 Agent 可能更需要文档而不是记忆：文档是可测试的，插件记忆要评估难得多。

## Rule 7：Local development is the weak point

Ball 说本地开发现在感觉很别扭（yucky）。每个 Agent 都应该能装、能跑自己需要的东西，远程机器上容易，笔记本上难——Agent 会在端口、数据库、缓存上撞车。

可行的办法：

| 办法 | 优点 | 缺点 |
|------|------|------|
| 每个 Agent 一个 Git worktree | 便宜、代码隔离 | 端口和数据库还是会撞 |
| 每个任务一个容器或虚拟机 | 完全隔离 | 启动慢、搭建多 |
| 远程开发机 | 能扩展到很多 Agent | 成本和网络延迟 |
| 端口和数据库名模板化 | 笔记本上能跑 | 得自己建、自己维护 |

## Rule 8：Do not iterate with the agent, make it build the iteration tool

反模式是 "咱们试试这个……啊不对，试试那个"。Ball 的替代："Generate 20 variations and a little page that lets me combine and test them."（生成 20 个变体，再加一个小页面让我组合、测试它们。）这对解空间和问题空间都适用："Give me 4 theories on why this is slow, then check all four."（给我 4 个为什么慢的理论，然后逐个验证。）

收益是你不再是循环本身。你只看一次对比页面，而不是读二十轮聊天。例子：
- 设计任务：要 20 个组件变体，加一个带开关的 HTML 页面。
- 性能 bug：要四个假设、每个假设一个 profiling 脚本、一个排名结果。
- 改 prompt：要一个测试集、一个运行器、每个变体一张分数表。

## Rule 9：Ask for irrefutable proof

Ball 称这是最大的改变。Agent 很擅长出证据：端到端 walkthrough、演示视频、前后对比数字。他的建议是：培养一种对"什么算证据"的直觉，并把 "this is not evidence"（这不算证据）写进 AGENTS.md。

可改编的片段：

```markdown
## Evidence rules
- "Tests pass" alone is not evidence. Show the failing test before the change.
- For UI changes, attach a screenshot or short recording of the real flow.
- For performance changes, attach before and after numbers from the same machine.
- "It should work" and "this looks correct" are not evidence.
```

这条和 Rule 15 是搭档：Agent 附的证据越多，你逐行读代码的需要就越少。

## Rule 10：Let the agents wait on you

如果你担心首 token 速度，Ball 说你大概做错了。拥抱异步：给 Agent 更大的任务、更野心勃勃、批量派，而不是来回聊。一次好会话的衡量标准是 Agent 无人看管能工作多久，而不是第一条回复多快。

实际做法：一次写一份更完整的简报（目标、约束、要什么证据），然后走开。同时排好几个任务——这又回到 Rule 7。

## Rule 11：Schedules, wake-ups, and heartbeats

Ball 直到自动化被关掉才意识到自己多依赖它。他的例子："Check the logs in 2 hours."（2 小时后查日志。）"Let me know once this is live."（上线了告诉我。）"Check every day at 9 am."（每天上午 9 点查一次。）

这些是小指令、大效果：它们把 Agent 从你操作的工具，变成一个有跟进职责的同事。如果你的 harness 有定时任务或循环模式，先用在三件事上：部署后检查、每天错误汇总、依赖或发布监控。

## Rule 12：Stop managing context by hand

Ball 的立场：每个花在想"你现在是 300k 还是 380k token"上的念头都是浪费。压缩（compaction）效果非常好。保持一线程一任务。他的线程从 46 条消息到 3,714 条不等。

研究支持这个方向：苹果关于自压缩 Agent 的工作表明，模型可以学会在不丢失主线的情况下总结自己的历史。注意：压缩可能丢掉你需要的细节。如果某个细节在整个任务里都重要，把它写进一个 Agent 会反复读的文件里，别只留在聊天里。

## Rule 13：Threads are company memory

Ball 的团队用 Amp，线程链接到 git 提交。有人问 "我们为什么这么做？"，线程就能回答。这也避免了重复劳动，因为队友能找到已经试过的东西。

任何工具都能近似做到：把线程或会话链接放进提交信息或 PR 正文里，并让 Agent 的 transcript 可以被搜索。

## Rule 14：Do not let agents write for humans

Ball 的规则是别让 Agent 替人写东西，有三个例外：调试步骤、事故报告、部分文档。理由是：写给人的文字带有语气和判断，Agent 担不起；而机械的文字没有这个问题。这是品味问题，很多团队会在别处划线。

## Rule 15：Review only code with high blast radius

这是最容易吵起来的一条。Ball 说他亲自复核的中心文件不到五个，加上数据库迁移。闭包还是类、类型是不是更整齐、变量名、甚至重复：他都不再关心。他只在四个答案都是 yes 时才复核：能跑吗、他能不能在"箭头图"那一层理解它、Agent 有没有端到端测过、一个月后他还能改它吗。

安全使用这条的办法是把爆炸半径写明白：

| 必须人工复核 | 可以略读或跳过 |
|---|---|
| 数据库迁移和 schema 变更 | 变量名和函数名 |
| 认证、权限、支付路径 | 闭包还是类的选择 |
| 公开 API 契约 | 重复的 helper 代码 |
| 所有人都依赖的少数中心模块 | 有端到端证据的内部重构 |

把你自己的五个文件列进 AGENTS.md，让你和 Agent 都知道哪些改动需要人。这个赌注只有在 Rule 9 的测试和证据要求够强时才成立。没有它们，"跳过复核"就是"跳过检查"。

## 底线

这份清单十五种说法其实是一个意思：别再当瓶颈。给 Agent 一个目标、让它看见真实系统、让它证明自己的工作，把你自己的注意力花在少数几个出错代价大的地方。争议会在 slop 和复核上。explainx 的建议是：Rule 3、4、8、9、10 现在就采用，风险低；Rule 2 和 15 只按你的证据和测试赢得的速度采用。

---

来源：
- Thorsten Ball, "How I work with Agents... as of October 2026", X, 2026-10-25 15:58 UTC. https://x.com/thorstenball/status/2114386050638336082
- explainx.ai, "How to Work With Coding Agents: 15 Rules (Oct 2026)", 2026-10-26. https://explainx.ai/blog/how-i-work-with-agents-thorsten-ball-15-rules-october-2026-guide
- AICoder, "X discussion: Thorsten Ball's 'How I work with Agents' lists 15 habits...", 2026-10-25. https://aicoder.com/news/news-20261025-x-discussion-thorsten-ball-how-i-work-with-agents

整理时间：2026-11-07。X 原帖逐字稿未拿到，本文以 explainx.ai 二手解读为准，交付时须说明。
