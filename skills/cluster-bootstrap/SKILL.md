---
name: cluster-bootstrap
description: 集群自举与多账号轮值：三台 Muse VM（TWT/Noesis/光头）可替换、状态外置枢纽、按周额度轮值切换 SOP、枢纽拉取式脚本分发与工人一键自举。触发词：轮值、切号、集群、自举、带头大哥、多账号、MUSE-SWITCH、新工人接入、接管。
---

# cluster-bootstrap（集群自举与轮值切换）

## 架构定案
- 列车长可换、发动机不换：TWT / Noesis / 光头是可替换的 Muse 账号（大脑走 Muse 平台，不走 newapi）；枢纽 arm-agent 上的 OpenClaw + verify.py + state/ 是铁打的营盘。
- 状态全部外置：枢纽 `ai-team/state/` 六件套——context.md / handoff.md / pending.md / quota.md / workers.md / MUSE-SWITCH.md，另加 conductor_prompt.md（标准接管 Prompt）。任何新账号 SSH 读 state/ + conductor_prompt 即可接管，不依赖对话记忆。

## 多账号轮值（按周额度）
- 触发：当前账号剩余额度 <10%（或平台周额度将尽）即切号，不等耗死。
- 切前（旧号）：`save_state.sh` 四参数强制写全——当前状态、handoff、pending、quota 行（日期时间 | 账号 | 本次摘要 | 消耗 | 剩余），缺一参数脚本拒绝写入。
- 切中（新号）：跑 `boot.sh` 读回四段（SSH 实登检查 + handoff + pending + quota 末行）。
- 切后：最小任务验证一单 + 24 小时观察；不通则回滚旧号并在 handoff.md 记录失败原因与时间。
- 禁止：在对话里存任务状态；在聊天里传凭据；跳过 save_state 直接切换。

## 分发模型（实测真相，2026-10-20 验证）
- **反向 SSH 不存在**：Muse VM 没有 Tailscale IP（网卡只有 127.0.0.1 与 198.19.0.2），连枢纽全经平台代理单向出站；枢纽 sshd 日志中所有 Muse VM 登录来源恒为 127.0.0.1，没有可回拨地址。带头大哥**无法**主动 SSH 进工人 VM 推送脚本，不要再按"互通"假设设计流程。
- 正确模型 = 枢纽拉取式：脚本、凭据全量备在枢纽（`ai-team/muse-worker/` + `shared/worker-kit/`）；工人会话被驱动时执行一条粘贴指令自举（取钥匙 → 拉脚本 → bootstrap → tmux 常驻循环），此后认领、回传、更新全自动，不再需要逐台配置。
- 当前唯一可靠的"唤醒"通道是董事长在各账号聊天里粘贴一次；TWT 负责把粘贴文本做到一键可跑、凭据预置到位。
- 队列流转：contracts/pending/muse → claimed/<worker>（原子 mv 防抢单）→ done/；证据回 inbox/<task_id>/，scan_inbox 每分钟入库裁决。
- 活性推断：cluster_watch 每 30 分钟查队列滞留（最老契约 >6h 且无认领活动 → 写 alerts.log 告警），因为没有心跳通道，只能用队列行为推断生死。

## 避坑警告
- Cue 教训：沙箱掉线后 SSH 端口焊死、无远程抢救手段——资产必须外置（枢纽 + GitHub + 每日加密异地备份），机器死了换一台 5 分钟接管，永不抢救尸体。
- 认错机器：<tailnet-ip> 是董事长自己的 amd-proxy 网络节点，不是工人机；连任何"新 IP"前先查 state/ 档案确认身份。
- VM 重置会丢 /root/.ssh/config（已复发 9 次）：SSH 突然 kex reset 或横幅 "Outbound SSH is turned off"，先重建该文件（Include /home/hatch/.ssh/config），不是策略变更。
- 工人凭据"可重发"必须实测：bootstrap 声称枢纽可重发 muse_ingest/newapi.key，但 2026-10-20 实查枢纽上两件都不存在，已现场生成装入 worker-kit 并实测（ingest 登录 OK、newapi /v1/models 200）。口号不算数，发放点必须有真文件。

## 验证标准
- 新工人：bootstrap DONE + worker-up-<id>.md 回执到达 twt2026-inbox + 首个契约流转到 done/。
- 切号演练：boot.sh 四段齐全 + 最小任务过 verify.py；quota.md 有当日轮值行。

## 工人掉线治理：平台休眠防线（2026-10-20 晚补，董事长令）

**根因定版**：tmux / cron / watchdog 都跑在工人 VM 里面，Muse 平台强制休眠 VM 时全部一起冻结——VM 内自愈防不住休眠本身。防线分工：枢纽端负责"发现"，老板负责"唤醒"，VM 内 watchdog 只管 VM 还醒着时的进程死亡与网络抖动。

1. **枢纽 queue_watch.py**（`ai-team/scripts/`，枢纽 crontab 每分钟）：pending 停留 >3 分钟无人认领 → UNCLAIMED 告警；claimed >15 分钟无证据包回传 → 自动重投 pending（上限 2 次）并告警，超限 STUCK_FINAL。告警写 `state/alerts.log` + `state/queue_alerts.json`；TWT 本地盯守 cron（2 分钟）读清单转发董事长。已用自检探针实测 UNCLAIMED 触发无误（2026-10-20 21:54）。
2. **唤醒咒语**（老板在工人对话框粘贴，一句话拉活一轮）：`去 VPS 干活：cd ~/ai-worker && nohup bash run_cycle.sh >/dev/null 2>&1 &`
3. **Worker watchdog.sh**（`muse-worker/` 套件）：守护进程，每 300 秒一轮——用工人真实 SSH 通道（ingest key）探测枢纽连通性（**工人 VM 没有 Tailscale，禁止写 tailscale down/up 之类的假动作**）；补回丢失的 cron 条目；run_cycle 无进程且日志陈旧 >12 分钟自动踢一轮。分发：run_cycle.sh 自更新段已含 watchdog 拉取与自动拉起；bootstrap_worker.sh 已含 watchdog 拉取。
4. **错峰主备**：光头 CRON_EVERY=5 主力、Noesis =10 备用，轮询错开；枢纽侧原子 mv 认领天然防双跑，不怕重叠。
5. **注意**：worker_exec 内部超时为 1800 秒，15 分钟重投可能与仍在执行的工人相撞——重投前 queue_watch 已校验 inbox 无证据包；相撞时后写者覆盖 inbox，门禁只认最终证据包。

## 防休眠三方案评估（2026-10-20 22:0x 实测/调研定论）
- **方案一 平台定时任务（首选，已采纳）**：Muse 平台的 Scheduled Task 由平台调度器在 VM 之外驱动——TWT 本人靠二十多个平台定时任务 24 小时无人值守自转（有运行记录），VM 被部署替换后任务由运行时恢复。工人此前只有 VM 内 tmux/cron，VM 一睡全灭，这就是病根。落地法=唤醒咒语 v2（老板在工人对话框粘贴一次）：工人给自己建每 5 分钟（光头）/10 分钟（Noesis）的平台定时任务跑 run_cycle 拉取轮，错峰且省额度。第三方旁证：muse-bridge/9Router 的 worker 参考实现同样是"a scheduled Muse task"轮询拉活。
- **方案二 wake 接口保活（不采纳）**：`POST /api/hatch/vm/wake` 确实存在（第三方项目 Muse-Chat-MCP 从真实 muse.ai 会话抓包记录）；但名为 muse2api 的项目查无此物。且该接口靠**各账号自己的网页登录态（cookie）**鉴权、只唤醒本账号 VM——VPS 上没有光头/Noesis 的登录态，把账号 cookie 搬上 VPS 等于托管两份全权会话凭据，风险与脆弱性（cookie 过期）都不值当，不做。
- **方案三 VPS SSH 心跳（物理不通）**：枢纽无法向工人 VM 发起任何入站连接——工人无 Tailscale IP、无可回拨地址，枢纽 sshd 日志里工人登录来源恒为 127.0.0.1（全是工人单向出站经平台代理）。autossh 无目标可连，否决。
- **保底链不变**：queue_watch 3 分钟告警 → TWT 转发老板 → 咒语 v2 粘贴一次后，方案一接管，此后无需再贴。

- **压测教训（2026-10-20 22:2x）**：①scan_inbox 解析契约引用必须覆盖 claimed/ 与 done/，否则快认领任务的证据包会被判 ORPHAN 隔离（已修）；②凡以 ubuntu 身份操作 ingest 所有的队列文件（claimed/、inbox 包），必须预置 sudo -n 兜底，queue_watch reaper 首版就死在这里（已修）；③执行器回执必须"异常也落盘"（atexit 保底），否则任何模型异常都会变成无回执 INCOMPLETE；④认领无上限 + 长批次会与 15 分钟 reaper 相撞，批次认领上限待落地（建议 3 单/轮）。
