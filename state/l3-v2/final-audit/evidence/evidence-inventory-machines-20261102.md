# 摸底证据：机器 / 服务 / InstaBox（只读，2026-11-02 13:44-13:45 CST）

来源：只读子任务 baf153d3，ssh arm + InstaBox API 只读 GET，未改配置、未删文件、未发消息/推送。

## 枢纽 arm-agent
- hostname arm-agent；Ubuntu 24.04.5 LTS，内核 7.0.0-1013-oracle，aarch64
- CPU 2 核；内存 11Gi（已用 8.8Gi，可用 2.8Gi，Swap 0）
- 磁盘 / 77G 已用 48G 63%；/data 49G 已用 34G 72%
- IP：Tailscale 127.0.0.1；内网 10.0.0.33、10.0.0.190；docker 网桥 172.17/172.18/172.19.0.1
- SSH 别名：本机 ~/.ssh/config 中 Host arm 与 Host arm-agent 均指向 127.0.0.1、User ubuntu

## arm-hub
- 事实：~/.ssh/config 中无名为 arm-hub 的独立 Host 条目；无独立 IP 被本次检查验证。
- 推断（未证实为独立机器）：arm-hub 为历史名称，不能算作与 arm-agent 不同的第二台物理机，不得重复计入算力。

## Muse VM 本机
- hostname htch-runtime；Linux 7.0.0-39-generic，x86_64；CPU 4 核；内存 7.7Gi（已用 7.5Gi，可用 276Mi，Swap 0）；磁盘 / overlay 7.5G 已用 167M 3%

## seat-muse 与 hub-local
- 事实：两者均指向同一宿主机 arm-agent（同一 hostname、同一 2 核/11Gi、同一 Tailscale IP）。
- 推断：同一物理机上的两个逻辑执行端点，不是两台独立机器，不可重复计算 CPU/内存与故障域。

## InstaBox 机器列表（GET https://api.instabox.sh/v1/machines，只读）
- API 返回 machines 共 5 台，nextCursor=None，无第 6 页。
- 统一规格（5 台相同）：4 vCPU、8192 MiB、persistentDisk 20Gi、模板 agent-browser 2026-10-15.4、free=true、autoSuspendAfterSeconds=1800、maxRunMinutes=480
- worker-1 m_agqv3bvofwxkfb2m4axoqjbju4：running / running，ready true
- worker-2 m_agqv3c5yhvcfa36clq23hu336m：suspended / suspended，ready false
- worker-3 m_agqv3n5d3ou3lloeoyv3zg5czq：suspended / suspended，ready false
- worker-4 m_agqv3n5estnryd66fhihzavtzm：suspended / suspended，ready false
- worker-5 m_agqv3n5fcdqga4mzdd65hcol5e：suspended / suspended，ready false
- worker-1 单机详情：rootDisk 16Gi，storage 20Gi（/data，persistent，synced，snapshotSeq 26），runStartedAt 2026-11-02T04:00:08Z，readyAt 同时刻，cooldownUntil 2026-11-02T03:45:12Z，oomKills 0，createdAt 2026-10-21T08:14:29Z
- **worker-6 至 worker-10：本次 API 列表中不存在，未验证，不得编造。此前 Registry 中的 worker-6 未在本次 API 返回中出现。**

## 服务与调度（枢纽，13:44）
- butler-loop：systemd active，MainPID 603043，running
- cron：seat_revive 每 2 分钟；supervisor_check 每 30 分钟；supervisor_worker 自测 5,35 分；agent-executor 每分钟 flock；five-step 每 2 分钟 flock；L3 Supervisor 每 5 分钟（日志末尾 supervisor ok tasks=6/7 decision_pending=7）；butler_heartbeat_check 每 5 分钟；sync_to_butler_out 每 5 分钟；butler-self-check 每日 07:00
- 进程快照（13:44）：butler_loop.sh PID 603043 运行中；一个 timeout 900 的 opencode 巡航会话运行中；butler_seat_revive.py 运行中；另有 timeout 600 的 opencode 会话
- seat_revive 日志末 3 条（13:40、13:42、13:44）：均为 [SKIP] reason=named_lock_busy
- Docker：openclaw-official-openclaw-gateway-1 Up 33 hours (healthy)；ai-stack-new-api-1、ds2api-smoke、vaultwarden、uptime-kuma、sub2api 等 Up 9 days（多为 healthy）
- 脚本存在：butler_loop.sh、butler_seat_revive.py、ceo-opencode.sh、supervisor_check.py、supervisor_worker.py、alert_push.sh 均存在，另有大量 .bak-* 历史备份（未逐一核对内容）

## 脑、队列、链路健康（13:44:33 brain-health.md）
- Zen 通道 opencode/deepseek-v4.1-flash-free（当前出口 127.0.0.1:10801）：DEAD
- 网关兜底 glm-4-flash-cc（newapi 直测）：HTTP 200
- 网关兜底 deepseek-chat：HTTP 503（死通道已禁用，预期 model_not_found）
- 席位最近选用脑：2026-11-02 13:42:39，opencode/deepseek-v4.1-flash-free（名义实选与通道健康不一致，见审计缺陷）
