#!/bin/bash
# gate-record.sh -- DISABLED (cutover 2026-11-10, S6)
# 写证据功能已禁用：agent 不能自己写 .done 标记（可伪造）。
# 证据只能由 gate-svc 产生：
#   记忆/KB 回执：hubctl search <seat> <slug> <query>
#   验签：hubctl verify <receipt_id>
#   plan-approved：只能由董事长经 gate-svc /approve 生成（hubctl approve）。
# 旧 .done 文件已作废（以数据库 receipts/plan_approvals 为准）。
echo "FAIL: gate-record.sh 写证据已禁用（2026-11-10 切运）。证据由 gate-svc 产生，agent 不写证据。" >&2
echo "  hubctl search <seat> <slug> <query>   # 取回执" >&2
echo "  hubctl verify <receipt_id>            # 验签" >&2
echo "  plan-approved 只能由董事长经 gate-svc /approve 生成" >&2
exit 1
