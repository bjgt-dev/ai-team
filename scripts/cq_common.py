"""cq_common.py — 中央队列唯一租约定义（cutover51 W3，单一真源）.

LEASE_SECONDS = 1200：唯一租约时长。c2-executor 与 queue_api 都 import 本模块；
全仓禁止出现第二个租约常量（grep 'interval .600 seconds' / '1200 seconds' 作为
canary 的一条；允许出现的唯一位置是本文件与 queue_api/c2-executor 对本模块的引用）。
围栏令牌：central_leases.lease_epoch，每次认领 +1（与 current_fencing_token 同步
递增）；worker 提交结果必须带 epoch，epoch 不匹配一律拒收。
续期：单条原子 UPDATE ... WHERE task_id=? AND lease_epoch=? AND lease_expires>now()，
并带 deadline 上限；续期返回 0 行 = 租约已丢，worker 必须立即终止子进程且不提交结果。
"""
LEASE_SECONDS = 1200
# 续期间隔：lease/3
RENEW_INTERVAL_SECONDS = LEASE_SECONDS // 3
# 任务时长上限（W3.4）：预计 >20 分钟且无分段 checkpoint 的 code 合同拒收（见 /gate/dispatch）。
MAX_ESTIMATED_SECONDS_WITHOUT_CHECKPOINT = 1200
