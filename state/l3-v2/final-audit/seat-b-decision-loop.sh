#!/bin/bash
# B 级定案席位重试环：只走既有 ceo-opencode.sh 机制，锁空闲时自动获得席位，不绕锁、不杀进程
for i in $(seq 1 60); do
  echo "$(date "+%F %T") attempt $i" >> /home/ubuntu/ai-team/state/l3-v2/final-audit/seat-b-decision-loop.log
  if timeout 700 bash /home/ubuntu/ai-team/scripts/ceo-opencode.sh "你是大管家（幕僚长下属，COO加首席架构师加安全官）。任务：最终审计 B 级定案，只定案、不实施、不修改任何文件。逐项定案以下三项，每项写明：通过、否决或修改、备份办法、明确回滚办法、2小时实施窗口、实测办法、独立复核办法。第一项：枢纽 state/l3-v2/l3_claim.py 增加 fencing，更新任务状态时必须校验 claim token，旧 token 覆写必须被拒绝；历史事实：L3-004-review3 与 L3-OP-UGC-001 两个 DONE 任务的 claim_token 为 None，禁止事后伪造 token，只能在定案中注明。第二项：枢纽 scripts/ceo-opencode.sh 的席位锁架构由长会话持锁改为短锁领单加独立执行加短锁回写，解决优先标志不能抢占已开始长会话的问题；holder 文件须加进程启动时间校验，防 PID 重用。第三项：枢纽 state/l3-v2/workers.json 中 worker-6 条目的处理：2026-11-02 13:44 InstaBox API 只读实测只返回 5 台机器（worker-1 至 worker-5），worker-6 不在列表中；定案删除、标注未验证、保留更正记录三者之一，不得直接删除历史记录而不留更正记录。输出全文即定案书，不得外发。" >> /home/ubuntu/ai-team/state/l3-v2/final-audit/seat-b-decision-loop.log 2>&1; then
    echo "$(date "+%F %T") seat_run_done" >> /home/ubuntu/ai-team/state/l3-v2/final-audit/seat-b-decision-loop.log
    break
  fi
  sleep 30
done
echo "$(date "+%F %T") loop_end" >> /home/ubuntu/ai-team/state/l3-v2/final-audit/seat-b-decision-loop.log
