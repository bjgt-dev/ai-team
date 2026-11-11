# 测试矩阵部分结果（沙箱，生产代码副本，2026-11-02）

方法：从枢纽复制 state/l3-v2/l3_claim.py 与 supervisor.py 到 /tmp/audit-sandbox，仅把 BASE 路径改到沙箱，未改逻辑；生产队列、脚本、cron 未动。生产队列实测（2026-11-02 13:5x，ssh 只读）：7 任务，6 DONE、1 PENDING（L3-AUTO-20261102-1313）；L3-004-verify-archive 已为 DONE（attempt 1，token 63c99089b6a89d6a），与早前 VERIFYING 口径不同，以本次实测为准。

## B06 队列重复领取 — FAIL
- 沙箱：同一任务先由 worker-A CLAIMED（attempt 1，token 22df71cc3eda9f5a），再由 worker-B CLAIMED，第二次直接覆盖 claimed_by 与 token，attempt 升到 2，无“已 CLAIMED 拒绝”守卫。
- 结论：重复领取可发生，租约与 token 形同虚设。

## B08 旧 token 覆写 / fencing — FAIL
- 沙箱：l3_claim.py 根本不接受 token 参数，任何调用者可把任意任务写成任意状态。无 token 的 stale 写入直接成功置 DONE。
- 生产印证：L3-004-review3 与 L3-OP-UGC-001 均为 DONE 且 claim_token 为 None；后者 attempt 为 1 但 token 为 None，说明存在绕过 CLAIM 直接置 DONE 的路径。禁止事后伪造 token，只能在定案中注明。
- 与 supervisor.py docstring 冲突：docstring 称“only current token may write status”“old process cannot overwrite (token check)”，代码中无此校验。

## 直接 DONE（无 CLAIM）— FAIL（与 B08 同根因）
- 沙箱：PENDING 任务不经 CLAIM 直接置 DONE 成功，attempt 保持 0，token 为 None——与生产两条 DONE/token None 记录形态一致。

## B07 过期租约回收 — PASS（沙箱）
- 沙箱：CLAIMED 且 claimed_at 为 3600 秒前 → 回收为 PENDING，token 清空，note 为“lease expired, reclaimed by Supervisor, old token fenced”；新鲜 CLAIMED 不受影响。
- 注意：回收后 token 清空不等于 fencing，因为写入路径本身不验 token（见 B08），旧进程仍可写入。

## 老化（PENDING 超 60 分钟升一级，上限 P1）— FAIL（未实现）
- 沙箱：PENDING 120 分钟的任务跑完 supervisor 后仍为 P3，无升降。docstring 称有 aging，代码中无 aging 循环。

## Supervisor 主流程锁 — 缺陷（待 B 级定案）
- supervisor.py main() 本身不持 flock（仅 run_locked 辅助函数用 flock，但 main 未调用它）。cron 每 5 分钟运行，并发运行可竞写 queue.json。
- docstring 另称“heartbeat file per task”，代码实际只用 claimed_at，无 heartbeat 文件。docstring 与代码不一致，须以代码为准，docstring 待修正。

## 待测（本轮未做，不得冒充结果）
- C01/C02 长会话占锁、C03 seat_revive 争锁、C06/C07 锁退出与异常回收、C08 并发领取不重复、D03 Worker 唤醒后状态确认、D07 InstaBox 409（本轮未实际发生，标 NOT TESTED）、E06 L3-004 第三轮回执缺失（流程层 FAIL，已有证据：第三轮 model-output 存在但 revival-receipt 未生成）。
