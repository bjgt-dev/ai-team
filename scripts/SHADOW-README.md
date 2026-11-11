# SHADOW-README — 工人队列告警「影子模式」变化监视器

日期：2026-10-21。出处：省额度方案 A 案急救包第 1 步（~/workspace/ceo-ops/quota-economy-plan-20261021.md 措施 M2）。
状态：**影子并行期**。现行 Muse 侧 cron `worker-queue-alert-watch`（每 2 分钟起会话转发告警）原样运行，本影子不发送任何消息、不改任何现行链路。

## 用途

在枢纽本地以零 LLM、零 Muse 额度复现现行转发链的「应唤醒」判定，把每个应唤醒事件落盘，为切换前的「漏唤醒 = 0」对照提供独立证据。若对照通过，后续才可把 2 分钟全价会话替换为「枢纽脚本判定 + 仅变化时唤醒」。

## 组件

- 脚本：`/home/ubuntu/ai-team/scripts/alert_shadow_watch.py`（枢纽 crontab 每分钟运行；flock 防重入）
- 影子日志：`/home/ubuntu/ai-team/state/shadow-wake.jsonl`（仅「有事件」时追加；无变化零写入）
- 自有状态：`/home/ubuntu/ai-team/state/.alert_shadow_state.json`（seen 集合 + alerts.log 字节偏移）
- 运行日志：`/home/ubuntu/ai-team/logs/shadow-watch.log`
- crontab 备份：`/home/ubuntu/ai-team/state/crontab-backup-20261021-1919.txt`（回滚 = 恢复该备份或删 crontab 中 shadow 一行）

## 比对口径（与现行链逐项对齐）

现行链的唤醒判定 = 每 2 分钟读枢纽 `state/queue_alerts.json` 的活动告警集，与本机 seen 文件比对：新出现 → 转发告警；消失且曾报过 → 转发恢复；无变化 → 静默。
影子端以同一数据源、同一集合差分语义复现，只提高检测频率（1 分钟 vs 2 分钟），事件类型一一对应：

| 影子事件 | 含义 | 对应现行链动作 |
|---|---|---|
| `baseline` | 影子启动基线（采纳当时活动集，历史不回放） | —（对照锚点） |
| `wake_new` | 活动集新增告警（task_id+kind) | 应转发一条新告警 |
| `wake_resolved` | 活动集告警消失 | 应转发一条「已恢复」 |
| `alerts_log` | `state/alerts.log` 新增行（增量偏移读取，含指纹） | 交叉校验用：queue_watch 事件行应与 wake_new 大体对应 |

每条记录字段：ts、event_type、task_id、kind、summary、fingerprint(sha256 前 16 位）、set_fingerprint。测试记录带 `"test": true`。

## 自验证据（2026-10-21 19:19 实测）

经环境变量 `SHADOW_QA_FILE` 指向临时测试文件（不碰正式 queue_alerts.json、不写 alerts.log)，注入假告警 `SHADOW-TEST-001 / UNCLAIMED` 后再清空，结果影子日志依次落：baseline → `wake_new`(test） → `wake_resolved`(test)；随后对真实文件再跑一次，无变化零追加。捕获链完整。

## 漏唤醒 = 0 对照方法（影子期内执行）

1. 每日（或影子期结束时）取三方记录对账：影子 `shadow-wake.jsonl` 的 wake_new/wake_resolved 事件、Muse 侧 seen 文件 `~/workspace/ceo-ops/hidden_files/queue_alerts_seen.json` 的增删、主聊天实际转发时间点。
2. 判据：现行链每一次实际转发（含恢复通知），影子日志中必须有同 task_id+kind 的对应事件，且影子 ts 不晚于实际转发 ts（影子频率更高，正常应更早）；影子多出的事件逐条查因（多为现行链 2 分钟采样错过的短命告警，属影子更优，不算错）。
3. 任一「现行链转发了、影子没记到」= 漏唤醒，立即停切换并查因。

## 影子期建议时长

方案口径：至少 48 小时且期间真实告警事件 ≥ 3 次才有统计意义；若 48 小时内真实事件不足 3 次，延长至攒够，或经董事长批准用一个受控测试契约走真实链路做一次全链演练后再判定。

## 红线（本组件永不做）

不写 alerts.log、不改 queue_alerts.json、不动 queue_watch.py 及冻结清单五件、不改 Muse 侧任何 cron、不发送任何消息。

---

# 第 2 步扩展（2026-10-21 19:26 上线）：留言板线 + 网关线

出处：省额度方案 A 案急救包第 2 步。做法：扩展同一脚本 `alert_shadow_watch.py`（备份 `alert_shadow_watch.py.bak-20261021-step2`），复用同一指纹/增量比对、同一状态文件（新增 `inbox_seen`、`gw` 键）、同一影子日志；新记录统一带 `source` 字段（`queue` / `inbox` / `gateway`，第 1 步的历史记录无此字段，按 queue 读）。枢纽 crontab 未改动——原每分钟一行直接复用。现行任何 cron/告警链仍一字未动。

## 留言板线（source=inbox）

- 对照对象（已按枢纽实际核实）：`/home/ubuntu/shared/twt2026-inbox/`——按 `shared/PROTOCOL.md`，此目录是 Noesis → Twt2026 的留言板，现行 `twt2026-ops-watch`（10 分钟）与 `noesis-inbox-watch`（20 分钟）读的都是它。注意命名陷阱：`noesis-inbox/` 是反方向（Twt2026 → Noesis），不在监视内。
- 判定：目录内文件集合逐分钟比对（指纹 = 文件名+大小+mtime）。新文件 → `wake_new`（kind=`INBOX_NEW`）；已见文件内容变化 → `wake_new`（kind=`INBOX_UPDATED`，对应 20 分钟线按 mmin 重报的语义）；task_id = 文件名。
- 非对称口径（有意）：文件消失不发 `wake_resolved`——现行两条线对留言都没有「已恢复」动作，影子端仅静默从 seen 中剔除。
- 基线：2026-10-21 19:26 采纳当时 9 个存量文件（baseline 记录 source=inbox），不回放。

## 网关线（source=gateway）

- 判定只用枢纽既有落盘状态，不新造探针：
  - 内存压力：`queue_alerts.json` 活动集含 `pm:gateway` / `PM_RSS_HIGH`（queue_watch.py 落盘）→ kind=`PM_RSS_HIGH`。该告警同时也会在 queue 线以普通告警出现，是有意的双视角，对账按线分开计。
  - 容器存活：`state/alerts.log` 中最新一行 `cluster-watch: gateway container NOT running` 的时间不早于 `logs/cluster-watch.log` 中最新一次 cluster_watch 完成（done）时间 → kind=`GATEWAY_DOWN`；其后 cluster_watch 再完成一轮且无新的 down 行 → `wake_resolved`。
- 状态翻转才发事件（ok→bad 发 wake_new、bad→ok 发 wake_resolved），持续异常不重复发。
- 基线：2026-10-21 19:26，rss_bad=False、container_bad=False（与当时实际一致）。

## 对账脚本（第 2 步新增）

- 脚本：`/home/ubuntu/ai-team/scripts/shadow_reconcile.py`，用法 `python3 shadow_reconcile.py [YYYYMMDD] [--include-test]`，输出落 `state/shadow-reconcile-YYYYMMDD.txt` 并打印。
- 输入：影子日志 + `state/alerts.log` + 两份现行链 seen 记录的副本，置于 `state/reconcile-inputs/`（`queue_alerts_seen.json`、`mailbox-seen.txt`，源在 CEO 本机 `~/workspace/ceo-ops/hidden_files/`，跑对账前需刷新副本；缺失时脚本明确报 n/a，不猜）。
- 口径要点：疑似漏唤醒 = 现行链记到了、影子从未捕获（按 task_id+kind / 文件名匹配，第 2 步起按 source 分线）；queue 线现行计数只是下限（seen 快照里已恢复的键会消失）；网关线现行链无落盘 seen 记录，以 alerts.log 原始事件行为参照集；alerts.log 中影子启动时间之前的参照行按设计忽略（影子不回放历史），并在输出中单列计数。
- 首跑（2026-10-21）：三条线疑似漏唤醒均为 0；网关线当日 2 行 PM_RSS_HIGH（12:02、13:00）属影子启动前事件，已按口径忽略并计数。

## 第 2 步自验证据（2026-10-21 19:26）

- 隔离自验（全部经 `SHADOW_*` 环境变量指向 /tmp 临时路径 + `SHADOW_SELFTEST=1`，不碰正式数据）：三线基线 → 注入测试留言文件 + 假 PM_RSS_HIGH 告警 + 假 down 行 → 依次落 inbox `INBOX_NEW`、gateway `PM_RSS_HIGH`、`GATEWAY_DOWN` 的 wake_new → 清除并补一轮 cluster_watch done → 三者 wake_resolved → 无变化再跑零追加。测试记录均带 `"test": true`、测试文件名含 TEST。
- 生产首跑仅追加 inbox/gateway 两条 baseline 记录，queue 线 seen 与 alerts.log 偏移原样保留，无误报。

## 三条线切换判据汇总（每条线独立判定）

| 线 | 现行链 | 影子起点 | 切换前必须同时满足 |
|---|---|---|---|
| queue | worker-queue-alert-watch（2 分钟） | 2026-10-21 19:19 | 影子连续运行 ≥48 小时；该线真实（非 test）事件 ≥3 次；对账疑似漏唤醒 = 0 |
| inbox | twt2026-ops-watch（10 分钟）+ noesis-inbox-watch（20 分钟） | 2026-10-21 19:26 | 同上 |
| gateway | twt2026-ops-watch 网关检查项 | 2026-10-21 19:26 | 同上 |

- 任一线出现「现行链报了、影子没记到」的实锤漏唤醒，该线立即停切换并查因（其余线不受影响）。
- 影子多报（现行链没报、影子记了）逐条查因：多为现行链采样间隔错过的短命事件，属影子更优，不算错。
- 判据全绿只代表「可以提案切换」，切换动作本身另行批准执行，不在本组件内自动进行。
- 第 2 步交付时点三条线状态：均在影子并行期，真实事件样本均为 0，还差时间（≥48 小时）与真实事件样本积累；对账基线已建（2026-10-21 三线漏唤醒 = 0，但当日无真实事件，该 0 仅证明管线通）。

## 判据修订（2026-10-21 20:11 董事长裁定，CEO 附议）
切换判据由「≥48 小时」改为：影子运行 ≥12 小时 + 每条线真实事件 ≥3 + 对账漏唤醒=0。理由：确定性差分比对，事件样本比墙钟时间更能证明不漏；无真实事件的线用带 TEST 标记的注入事件补覆盖，仍不足则单独延长该线。原 48 小时口径作废。
