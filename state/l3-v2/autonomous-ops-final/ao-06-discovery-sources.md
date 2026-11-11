# 06 主动发现指标与数据源对照（草稿，待 SSH 恢复后存入 autonomous-ops-final）

原则：每类指标必须有实际数据来源；没有监测数据的指标不得假装已监控。以下来源均为 2026-11-02 已实测存在的文件或接口。

| 指标 | 数据来源 | 当前状态 |
|---|---|---|
| 任务队列与长期积压 | state/l3-v2/queue.json；state/decision-queue.json | 有；决策队列 pending 7 超阈 |
| CLAIMED/RUNNING 超时 | queue.json claimed_at + Supervisor 1800 秒租约回收（沙箱 PASS） | 有；回收逻辑已验，fencing 待实施 |
| FAILED 与 QUARANTINE | queue.json 状态字段；contracts/failed 目录（当前不存在，首用时创建） | 部分；failed 目录缺失本身记为待补 |
| Worker 在线状态 | InstaBox API GET /v1/machines（只读实测 5 台） | 有；worker-1 running，其余 suspended |
| Worker 唤醒失败 | InstaBox exec 返回码与 request-id（已有 req_wm6f… bad_request 正例） | 有；须留存完整正文与头（D07 规范） |
| 席位占锁与长会话 | /tmp/ceo-opencode.lock + holder 文件；butler 日志 | 有；长会话不被抢占为已知缺陷 |
| 模型服务与网关状态 | state/brain-health.md；brain-active.log | 有；Zen DEAD，网关 glm-4-flash-cc 可用 |
| Oracle CPU、内存、磁盘 | ssh arm 实测（2 核、11Gi、/ 64%、/data 72%） | 有；阈值 85% |
| 关键服务状态 | systemctl（butler-loop active）；docker ps（openclaw 网关 healthy） | 有 |
| 知识资产归档失败 | state/knowledge-assets 归档目录与脚本退出码 | 有；归档失败进恢复流程（规范已定） |
| 项目目标长期无进展 | state/l3-v2/goal-tree.json；G1/G2/G3 验收标准 | 有；每日扫描待落地（设计，非代码） |
| 免费额度与资源到期 | InstaBox 免费模板参数；Oracle Always Free 口径 | 部分；到期日无统一台账，待补 |
| 未完成的董事长决策 | state/decision-queue.json pending 列表 | 有；7 条 |
| 自动化流程异常 | supervisor.log；five-step-executor.log（12:10 后约 3 小时未更新，需查因，不得直接断言已死） | 部分；五步链停滞原因待查 |

## 自动发现规则（设计稿，代码实施待第二批定案）
- PENDING 超阈自动建复核任务（Supervisor 已有决策队列积压自动发现，L3-AUTO-1313 为正例）。
- RUNNING 超时先查执行主体，再回收或切换，不得直接改 DONE。
- 同类错误重复发生建系统故障事件（decision-queue repeat 计数为现有信号）。
- 项目长期无进展启动经营复盘（G1/G2/G3 每日扫描，见每日简报规范）。
- 模型调用失败走已批准的免费备用通道；备用通道也失败则任务进等待队列并留 note。
