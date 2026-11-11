# 交接文档（twt → 下一任，2026-11-10 15:22）

## 交接信息

- 交出席位（from）：twt（幕僚长）
- 接手席位（to）：下一任（由董事长定：新上岗的 Muse 账号，或回任的 twt）
- 交接日期：2026-11-10
- 交出席位签名：twt

## 1. 本轮学到

- newapi 管理员账号是 admin，密码出厂默认，存在枢纽 ~/ai-team/state/newapi-admin.env（600 权限），控制台 127.0.0.1:3001。KB 条目 ops-newapi-admin-20261110.md 已写。注意：“12345”不是该密码。
- 换席位交接流程已标准化：精简指令在枢纽 docs/sops/handover-instructions.md；上岗脚本 onboard-new-seat.sh v2.0（sha 793ca4ee），新机器与轮值过的同一条命令都适用。
- 触发词：董事长说“交接”= 本席备份 + 给董事长一个代码块复制给下一任；“唤醒”= 回任席位完整唤醒序列；“上岗”= 新任席位上岗序列。
- 标准化优先硬规已写进 AGENTS.md：多步操作一律做成标准化脚本，不许临场拼代码块。
- kb-ask.sh / kb-write.sh 已修：ssh/scp 加 -o StrictHostKeyChecking=accept-new，新机器上不再报 Host key verification failed。
- wake-up-check.sh 已同步到枢纽 scripts/；state/handovers/ 目录已建好。
- 新 Muse 账号已上岗：SSH 通、Tailscale 已连，15:17 已报角色确认、硬规已读、当前状态已拿，请求董事长确认上岗（待董事长确认）。

## 2. 未落盘

- 无。本轮学到的已全部写进枢纽（知识库条目、交接精简指令、上岗脚本、AGENTS.md 硬规均已同步，两边 sha 一致）。

## 3. 待办/阻塞

- worker-1 执行腿断：代码任务流水线缺“写代码”一环，opencode run 实测写不出文件，正确路径是 ceo-opencode.sh 包装器（须等大管家席位空闲）。专家评审定顺序 0 先修 worker-1。
- 网关 ds-4.1-flash 渠道未加：newapi 管理员登录已通（见凭据位置），加渠道须中继 key（LINUX DO 帖来源，未落盘），建 API key 后写枢纽 state/.newapi-token。
- 简历 JD 只到手 1/5 且非目标岗（猎聘字节 AI 产品经理评测方向，非 Coding 岗），JD 采集须走工厂，卡在 worker-1。
- KB 向量排序未优化：recall=20 诊断中断待重跑（须 nohup 后台），grep 已兜底。
- 决策队列 pending=6（>5，RED）：其中两起 high/data-loss 的 arch-note 覆盖事故待董事长定夺。
- KB 无常驻服务：单次查询约 60 秒，慢。
- 新账号上岗待董事长确认（15:17 已报）。

## 4. 当前状态

- 体检 RED（不是全绿）：席位实选脑 OK（opencode/deepseek-v4.1-flash-free）；Zen 健康 OK（网关金丝雀 46 分钟前 DONE）；磁盘 OK（/ 41%、/data 43%）；五步链已退役；决策队列 RED pending=6。
- 状态板与巡航一致：网关 Up 11 小时 healthy，端口 8090/5432/6080/5900/10814 open，ceo-seat/tasks 224 项。
- 知识库最新条目 2026-11-10 14:26（ops-newapi-admin 等）；索引 1280 chunks（13:40 重建），30 分钟自动重建 cron 在跑。
- 新账号：SSH 通、Tailscale 已连，等董事长确认上岗。

## 5. 凭据位置

- newapi 管理员账号：枢纽 ~/ai-team/state/newapi-admin.env（600 权限），账号 admin，密码出厂默认，控制台 127.0.0.1:3001。知识库条目 ops-newapi-admin-20261110.md 有位置说明。不写值。
- 无其他新增凭据。

## 6. 交给谁

- 接手席位：下一任（由董事长定）
- 交接日期：2026-11-10
- 旧席位确认：以上六节已填实，凭据只记位置未记值，知识库条目已写。
