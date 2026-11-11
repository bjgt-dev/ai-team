# 交接文档（twt → 下一任，2026-11-11 14:32）

## 交接信息

- 交出席位（from）：twt（幕僚长）
- 接手席位（to）：下一任（由董事长定：新上岗的 Muse 账号，或回任的 twt）
- 交接日期：2026-11-11
- 交出席位签名：twt
- 交接 SOP：枢纽 docs/sops/handover-instructions.md（sha 7d4427b5，本席与枢纽一致，2026-11-11 下午复核）
- 上岗脚本：枢纽 ~/ai-team/scripts/onboard-new-seat.sh（sha 6bc7344e，本地镜像 ~/workspace/tools/onboard-new-seat.sh 同 sha；注意：与 2026-11-10 交接文档所记 sha 793ca4ee 不同，脚本已被更新，以本次实查为准）

## 1. 本轮学到

- Plan A：plan_id 派发链已修复并切换（2026-11-11 14:09–14:13 本席独立验收通过，董事长六项验收标准全过）。根因：批准（plan_approvals）与任务（central_tasks INSERT）从未连起来，合同从来没有 plan_id 字段；切运（2026-11-10 18:43）以来 0 个 code/script 任务成功认领过。修复 A-1 三处（taskcontract_gate.py 条件校验、dispatch_task.py 查库验存在、gate-svc.py 加第 7 门 plan-id-exists，审计 19/19 WIRED）。失败账本 failure_id=13（类别 4）。KB 条目 knowledge/收件箱/plan-a-planid-fix-complete-20261111.md（sha f8605a81，实时可查）。决策包 g5-planid-fix-decision-package-20261111.md（sha c2bc7a49）；最终回报 g5-planid-final-report-20261111.md（sha b0cb1393）。
- Plan A 同轮修好两件尾巴（董事长 13:55 “彻底修好”令）：① 队头毒丸 258fd357 已隔离为 QUARANTINED（pipeline-disposition 补 quarantine-ready 动作，审计 state/outbox/disposition-258fd357-20261111T060013Z.md）；② /act systemctl 修通（polkit 规则 /etc/polkit-1/rules.d/49-gatesvc-manage-units.rules，仅 gatesvc 用户、仅 gate-svc.service / openclaw.service、仅 restart/start/stop/reload；实证重启 PID 4067439 → 4069537；NoNewPrivileges 未动、unit 文件未改）。残留：queue-api 用户级重启路径仍被拦，只修了系统 unit 主路径。
- 大管家 intake 文件名：只认 ceo-seat/tasks/ 下的 review-*.md，butler-decision-request-*.md 它看不见（模板 intake 312 分钟无回覆的根因）。KB 条目 butler-intake-naming-20261111.md（sha 200b9b70）。
- gate 插件拦死大管家全部写盘：ceo-seat/.opencode/plugins/gate.js 要求每会话有 gate-svc kb 回执；切运后零真实 verdict、零札记新节。KB 条目 gate-plugin-blocks-butler-writes-20261111.md（sha d9e53356）。修复方向未定：须先诊断本席跑 hubctl search 时即便 files_matched=0 也会发回执、为何会话拿不到回执；董事长已授本席自决，不再问。插件是安全件，定方案前不许擅自改。
- Eval verdict 模板已确认生效（2026-11-11 13:05–13:06，本席依董事长“你拍板”授权确认）：定稿 ceo-seat/state/butler-verdicts/eval-verdict-template-20261111.md（sha 9f0cf6e7）；verdict 取值定死 eval-通过，eval=true，conditions=[]，valid_until=issued_at+24h；一份 verdict 只授权一次派发，绑定四元组（eval_run_id，eval_id，task_slug，contract_hash）；有效期至 Gate 1–4 验收完成或 2026-12-11 先到者为准。生效 ≠ 能派发：还差 G1 签发代码 + G2 门禁接线 + G3/G4 接线。
- EDD 与 Eval OS 对照已入库：KB edd-eval-os-mapping-20261111.md（sha e9b8d4e2）。LangChain EDD 帖三步与 Eval OS 同路；三个可直接用的缺口：合同缺“不做/保持 intact”段、无 held-out 集、生产侧在线评测属三阶段以后；LangSmith/Harbor 是他家营销不用买，自托管方案不变。
- 03.md《幕僚长提示词与报告模板》已采纳（2026-11-11 09:24 董事长发文）：信息翻译八问、证据五级、决策过滤、防重复错误、复杂度控制、汇报格式 + 四套模板。枢纽副本 state/03-chief-of-staff-report-templates-20261111.md（sha dbe338ca）；规则已写进枢纽正典 docs/AGENTS.md（sha 4e3a074e）与本地 AGENTS.md。本地根 ~/AGENTS.md 不是稳定真源，新规则须先写枢纽正典再同步本地，否则唤醒拉取会覆盖丢失。基建冻结未取消。
- 知识库七类索引已建：knowledge/驾驶舱/《知识库七类索引-20261111.md》（sha 9fe8e9f7）。结论：PRD 缺（三条业务线均无成文 PRD，G2 PRD 董事长 10:07 定先不做）；技术架构有；SOP 最全；产品资料只有散件；项目档案只有散件；评测规范无总文档；历史报告只有近期散件。
- 交付物标题索引已建：state/deliverables/INDEX.md（sha 872f392b），56 件中 52 件已确认标题、4 件标未知（不猜）。
- implementation-gate.sh 必须在本席本地跑：在枢纽上跑会因脚本内嵌 ssh 自连失败而全 FAIL（2026-11-11 13:03 实踩一次，零副作用）。
- LINUX DO 帖 3152964 已判不值得接（免费公益家宽代理，密码轮换/限速/白名单/公开免费，流量走别人家宽不安全，与三条商业线无关）；Karing 真机实测证伪本席“格式都认”的说法（通用订阅只导入 2 条 vless 且都不通）。枢纽 Chrome 档案 /opt/model-profiles/linuxdo 已有董事长 LINUX DO 登录态（用户名 Noesis，信任等级 1）。
- verifier 崩溃事故已修复（2026-11-11 12:14 验证通过）：c2-verifier 自 2026-11-10 23:09 起每分钟崩一次，IndeterminateDatatype 累计 775 次，9 个任务卡 VERIFYING 约 13 小时。根因：F1 给失败事件盖 failure_class 戳的 SQL 两参数无类型标注。修复两层：%s → %s::text；GRANT UPDATE + SELECT ON central_events TO cq_timer。教训：修完一层要再看日志；写库语句改完须核对执行角色的全部权限。
- G2 三份 llm_doc 合同另有两件缺陷（挡 G2 试跑、不挡三考试）：① decision-code-gate 要求所有非 eval 类型声明 code_files/evidence_files，G2 三份合同都没有；② verifier gate1 按合同 output_path 字面查存在，执行器写 <UUID>.md，llm_doc 到不了 DONE。须另案跟进。
- Wonder 申请与订阅（2026-11-11）：Wonder 没有单独“申请”通道，是 Muse 付费档给的模型；订阅实查无订阅，免费周额度已用 95%，2026-11-16 23:37 CST 重置；早期访问计划已提交（delivery_confirmed=true）；董事长最终决定先不订。KB 条目 wonder-subscription-decision-20261111.md（sha d08846ef）。
- 董事长授权升级三连（本轮）：① 12:22 “这么久了，你去修啊”（等大管家 5 小时无回覆），本席改走“本席起草、大管家审阅”；② 13:05 “你拍板”，模板确认权交本席；③ 13:55 “彻底修好”，Plan A 两件尾巴不等最终回报决策卡、本轮内修好。教训：卡点拖久了要自己下手修，不要只催办+等。

## 2. 未落盘

- 接线协调员 ec099fb7 仍在跑（见第 3 节）：其 G1/G2/G3/G4 接线结果与三考试派发结果尚未落盘；下一任须先实查 DB/文件状态再向董事长汇报，不许把“协调员 completed”当“任务完成”。
- gate 插件修复方向未定（须本席先诊断 /search 疑点，见第 1 节）。
- 2026-11-11 当日日志（~/memory/2026-11-11.md）尚未追加交接这一段；MEMORY.md 现 23,999 字节，仍在 25,000 红线内但已接近，加内容须先压措辞。
- 其余本轮学到已全部写进枢纽（本节第 1 节所列 5 条新 KB 条目均已写并验证可查；交接文档本身即为落盘）。

## 3. 待办/阻塞

- 三考试派发（当前最急，进行中）：接线协调员 ec099fb7 自 2026-11-11 14:13 在跑（G1 签发代码 + G2 门禁接线 + G3/G4 最小接线 → Case A EVAL-001 → B EVAL-005 → C EVAL-003 逐个派发）；二阶段批准 plan_id 23085d4b-d440-4b07-b68d-1a6656dfa9a7 于 2026-11-12 06:48 CST 过期，派发须在此之前，逾期须董事长重新批准二阶段。14:31 实测已有新认领 534b68d3（14:29:43 CST CLAIMED，带 plan_id）。回报到后须实查再报董事长。之后：Gate 1–4 验收 → 五件交付物转正 + 证据包 + 对比报告，用 03.md 模板报董事长。未全过不得扩到 12 个 Case、不得搭 Dashboard。
- Plan A 切换后密集观测（基线 2026-11-11 14:08：tasks 1328、with_plan 3、leases_with_plan 12）：继续看 plan_id 写入率、认领成功率、拒绝原因分布、错误放行、积压变化、Worker 断连及报警。
- gate 插件修复：blocked，待诊断后自定方向（见第 1 节）。
- U-19 item4 待董事长定：m5 第 4 项验收失败——决策队列 pending 无人认领且“默认动作”无字段无执行记录。选项 A（twt 建议）：授权 twt 清掉 watchdog 技术类待定并处置 L3 那 1 项、补默认动作字段与记录；选项 B：维持现状逐条批，item4 持续红。
- G2 试跑另案：三份合同、kids-iteration5-1、dankoe-research 重跑、9 个解冻任务——Plan A 修复后仍须董事长逐批 /approve 新 plan（现有批准不覆盖）+ verdict 链；解封后 G2 重派须新 attempt 号；dankoe 还须 codegen 先种代码；周报 cron 须按解封日改 --start/--end。另 llm_doc 两件缺陷（见第 1 节）须另案跟进。
- xiangyu-workflow 定类：两句话已备好，等董事长定类。
- 专家通道：ChatGPT 卡 Cloudflare Turnstile 且密码不在任何保险库；DeepSeek 主号会话过期且密码自动化是团队红线。须董事长在枢纽 VNC 集中登录场次亲手登一次；专家评审目前只能 Claude + Gemini 降级跑。
- 故障转移 D1/D2/D10 未批；百炼 27b 2026-11-18 到期接力（D9）有时限。
- 简历任务卡工厂；SOP 9 阶段，意见批量制（说“改”才放行）；本地两份 PDF 在 workspace/user/files/（AI.pdf、resume-optimized-20261109-v31.pdf，董事长评现行版“写的太差了”）；投递顺序字节 Coding → 美团 Agent → 小米 Agent → 京东 Agent → OceanBase，未投递。孩子年龄/性别信息未确认，已略去。
- 儿童项目：迭代 1–4 DONE；目标产品 Ling 灵宇宙小方机；APK v1（826KB）下载链接已于 2026-11-10 16:54 过期，等真机验收；隐私口径未拍板、板子下单无回音、英语 ASR 须孩子真实语音实测。
- 其余未决见 MEMORY.md（Meta 登录确认、枢纽空间 3 个未建、Zoho 待点头、极客奶爸日记 r5 发不发、X @ai_money_intel 掉线待重登、Kindle 推送、Numa 扫码、Cue 账号未闭环、Google 密码外泄须逐个改）。
- 新账号上岗待董事长确认：2026-11-10 15:17 已报角色确认、硬规已读、当前状态已拿，请求董事长确认上岗（截至交接时尚未回复确认，不得默认已上岗）。

## 4. 当前状态（2026-11-11 14:27–14:31 实测，凭记忆讲现状 = 事故）

- 唤醒体检 14:27 RESULT: RED：ssh 通、配置在位；席位实选脑 my-gateway/agnes-3.0-flash（14:17）；Zen 健康 RED（网关金丝雀超 60 分钟未 DONE，117 分钟前——本轮新变红项，下一任须盯）；决策队列 RED pending=7（>5，已知积压）；磁盘 / 58%、/data 45%；五步链已退役；注册表 ok=71 fail=0 无漂移；六件已拉取（MEMORY.md、AGENTS.md、status-board.md 有更新）。
- gate-svc：PID 4069537，health {"ok": true, "mode": "enforce"}（14:31 实查）。
- 中央队列 14:31 实测：tasks_total 1333、with_plan 4、DONE 1234、QUARANTINED 95、READY 2、VERIFYING 1；leases_total 3261、with_plan 13；plan_approvals 7 行（最新 b1b66e17 g5-planid-dispatch-fix-20261111 2026-11-11 05:03:40Z）。关键任务：3a3c3f44 DONE（plan_id=23085d4b）；96ffd62f / fb9bde7a QUARANTINED（均因合同缺 test_files，预期内）；258fd357 QUARANTINED（plan_id 仍空，未回填）；新认领 534b68d3 CLAIMED（14:29:43 CST，带 plan_id，接线员派发进行中）。
- Plan A：已修复、已独立验收、已切换（14:09–14:13）。模板已生效。接线协调员 ec099fb7 运行中（14:13 派，15 分钟前仍在写 Case A 测试文件并跑库查询）。
- Wonder：早期访问已提交；订阅先不订；免费额度 95% 已用，2026-11-16 23:37 CST 重置。
- 基建冻结未取消；切运收尾残留（capability-matrix.md 未向董事长正式回报、S5 缺口、O4 fuzz 未建等）属冻结范围，不主动开工。

## 5. 凭据位置（只记位置，不记值）

- newapi 管理员账号：枢纽 ~/ai-team/state/newapi-admin.env（600 权限），账号 admin，控制台 127.0.0.1:3001。KB 条目 ops-newapi-admin-20261110.md 有位置说明。不写值。另：2026-11-10 13:26 董事长在聊天里发过一个 newapi 管理员密码，实测不正确，未落盘、未入记忆，勿用。
- gate chairman token：<chairman-token-path>（root:chairman 0440）；用法 GATE_CHAIRMAN_TOKEN_FILE=<chairman-token-path> hubctl approve <task_slug>；ubuntu 直接读 Permission denied，须 sudo。令牌值永不在聊天里出现。
- gate key：/etc/noesis/gate.key（root:gatesvc 0440）。
- 中央队列 DSN：枢纽 ~/ai-team/state/.cq-dsn（用户 cq_timer，权限有限）。central_queue 在 Docker 容器 ops-pg（PG 18.6）里；~/ai-team/state/central-queue.db 是 0 字节空文件，勿误认。
- codegen newapi token（ID 22）：枢纽 <newapi-token-path>（600）。
- 枢纽 SSH：ssh arm-agent（HostName <tailnet-ip>，User ubuntu，ProxyCommand /home/hatch/bin/ts-proxy-noauth.sh，IdentityFile ~/.ssh/id_ed25519）；VM 更换会清 /root/.ssh/config，恢复脚本 ~/workspace/tools/restore-ssh-arm-agent.sh（wake-up-check.sh 内含同款自愈）。
- LINUX DO 登录态：枢纽 Chrome 档案 /opt/model-profiles/linuxdo（用户名 Noesis，信任等级 1）。
- 枢纽桌面 VNC：https://vnc.noesis.ccwu.cc/vnc.html?autoconnect=true（Xvfb:99、x11vnc 5900、websockify 6080、cloudflared 命名隧道）。
- 网站登录（Secure Vault）：github.com、oraclecloud.com、qq.com（密码）。
- 无其他新增凭据。

## 6. 交给谁

- 接手席位：下一任（由董事长定）
- 交接日期：2026-11-11
- 旧席位确认：以上六节已填实，凭据只记位置未记值，本轮新事实已写进知识库（5 条新条目均已验证可查），交接文档已放枢纽 state/handovers/。
- 董事长转发：上岗脚本枢纽 ~/ai-team/scripts/onboard-new-seat.sh（sha 6bc7344e）+ 聊天里的交接代码块，董事长只复制转发。
