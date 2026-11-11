## 2026-11-03 切换窗口续（timers→停用旧路→观测开始）
- 预检：crontab 与 chair05 备份一致（67 行）、line2 queue_watch、line10 patrol_det、cq-run 与本机草稿同 hash（659d4b34…，初版缺 +x 已 chmod +x 补齐）、cq-csv 同 hash（18b4ab60…）、.cq-dsn 600/112B、READY 7、leases 0，ALL-PASS。
- 装 timers：crontab 67→69 行（cq_claim、cq_sweep 各每分钟，cron_flock 串行；verifier 无扫描模式，按任务调用）。
- 首轮实测初版 FAIL（无 log、无 claim）：根因为 state/logs/ 目录缺失，cq-run 重定向失败（line 17），非 claim 路径缺陷；mkdir -p state/logs/ 后手动 claim 成功（exit 0，claim 1 条 93046390…、fencing 1，log 有 claimed 行，DB CLAIMED=1/READY=6/total 7，leases ACTIVE 1、10 分钟租期），VERDICT FIRST-RUN-PASS。教训：预检须验脚本写路径目录是否存在，不只验脚本本身。
- 停用旧路：dispatcher timer 经 sudo -n disable（plain 因交互认证失败）→ disabled/inactive；crontab line2 加 #DISABLED-20261103-CHAIR05 注释；patrol line10 与 cq 两行完好；crontab 69 行不变。cruise 无独立停用（前文差异记账）。
- 观测开始：以 central_tasks/central_leases/cq-csv.log 为观测对象；成功判据非 DONE（暂无任务执行器），而是 claim→租期满→sweeper→RETRY_WAIT→READY 循环无丢失、无误 QUARANTINED、无错误；租期约 10 分钟。
