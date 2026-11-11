# 02 重复认领与 fencing 修复设计（待大管家定案，尚未实施）

状态：设计稿，非已实施代码。实施前必须由大管家定案（备份、明确回滚、2 小时窗口、实测、独立复核）。B 级定案席位重试环进行中（2026-11-02 13:54 第 10 轮仍 seat_lock_busy，巡航会话占用中）。

## 缺陷基线（沙箱实测，生产代码副本，2026-11-02）
- B06 FAIL：已 CLAIMED 任务可被第二个 Worker 直接覆盖，attempt 递增、token 与 claimed_by 被改写。
- B08 FAIL：l3_claim.py 不接受 token 参数，任何调用可把任意任务写成任意状态。
- 直接 DONE FAIL：PENDING 任务不经 CLAIM 可直接置 DONE，attempt 0、token None——与生产两条记录形态一致。
- 租约回收 PASS（沙箱）：CLAIMED 超 1800 秒可回收为 PENDING 并清空 token；但回收不等于 fencing，因为写入路径本身不验 token。
- 老化未实现：PENDING 120 分钟仍为 P3；supervisor.py docstring 宣称 aging、heartbeat、fencing、flock，代码中均无对应实现或主流程未持锁。

## 修复规则（董事长最终授权第三章，逐条对应）
1. 已 CLAIMED 的任务，第二个 CLAIMED 必须被拒绝（返回失败，不改写 token、claimed_by、attempt）。
2. 一切状态更新必须携带 claim token 参数；缺 token 直接拒绝。
3. token 绑定 task_id + attempt_id + claimed_by；三者任一不符拒绝。
4. 过期（租约超 1800 秒且已被 Supervisor 回收）或已失效 token 拒绝；回收后旧 token 立即失效。
5. 旧 Worker 恢复后持旧 token 写入，拒绝（新持有者的 token 已替换）。
6. 租约续期接口必须验证原持有权（token + claimed_by 一致才续期，否则拒绝）。
7. 任务失败（执行退出码非零、交付物缺失、复核 FAIL）不得进入 DONE，只能 FAILED/QUARANTINE/BLOCKED。
8. 并发领取必须原子化：同一 flock 内完成“读状态—判可领取—写 token—落盘”，禁止读后写前的竞态窗口。
9. 过期任务只能由 Supervisor 回收（回收写 PENDING + note + token 清空），其他主体不得直接回收他人任务。
10. 原有有效任务与历史证据不得破坏：修复只改写入校验逻辑，不批量改写历史队列；历史 DONE 记录按第 03 号报告逐条审计标注，不删除、不批量修改。

## 老化机制（写进代码，不只文档）
- PENDING 每超 60 分钟升一级，上限 P1；升级需写 note 与时间戳，不得静默改优先级。
- 测试矩阵：P3 等待 0/30/61/121 分钟、P2 等待 61 分钟、P1 已达上限不再升、READY/CLAIMED/RUNNING 不参与老化；以沙箱实测结果为准，禁止只改文档宣称。

## 回滚
- 实施前备份 l3_claim.py、supervisor.py、queue.json 至 state/l3-v2/backup/（带时间戳）；回滚 = 恢复备份文件 + 重启相关 cron，不删除队列与归档记录。
- 回滚触发：任一验收测试 FAIL、队列出现重复认领回退、Supervisor 连续两轮报错。

## 验收标准（实施后）
- 沙箱 B06/B08/直接 DONE/续期/旧 token 五项全部拒绝或按规则通过，方可进入生产灰度。
- 生产灰度：先以一个低风险测试任务走完整 CLAIM→RUNNING→DONE 链，token 全程可查，再开放并发写入。
- 在验收通过前，生产并发写入保持暂停：Supervisor 只读监控 + 独立测试任务；不删除已有队列记录。
