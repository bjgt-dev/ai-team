#!/bin/bash
# B 级定案第二批席位重试环：只走既有 ceo-opencode.sh 机制，锁空闲时自动获得席位，不绕锁、不杀进程
for i in $(seq 1 60); do
  echo "$(date "+%F %T") attempt $i" >> /home/ubuntu/ai-team/state/l3-v2/final-audit/seat-b-decision-loop2.log
  if timeout 700 bash /home/ubuntu/ai-team/scripts/ceo-opencode.sh "你是大管家（幕僚长下属，COO加首席架构师加安全官）。任务：最终审计 B 级定案第二批，只定案、不实施、不修改任何文件。逐项定案以下四项，每项写明：通过、否决或修改、备份办法、明确回滚办法、2小时实施窗口、实测办法、独立复核办法。第一项：枢纽 state/l3-v2/supervisor.py 的 docstring 与代码不一致：aging、heartbeat、fencing、flock 四处 docstring 宣称与代码实际不符，定案以代码为准还是补实现，其中 aging 与 fencing 属功能缺失，定案是否补实现；另 main() 不持锁、并发竞写 queue.json 的问题一并定案。第二项：事件规则表，董事长 2026-11-02 14:07 已批准吸收内化落地：Supervisor 增加事件路由层，字段为来源、类型、目标执行主体、skill、并行数、通知条件（tell=needed 时才通知）。第三项：事件去重，同一事件（来源加类型加对象）重复到达时只唤醒一次，防止多 Agent 同时改同一件事。第四项：发布后线上核实，任何发布或上线类任务完成后，必须去线上核实版本真的变了才算结束，不得以生成成功冒充上线。输出全文即定案书，不得外发。另请注意：第一批定案（fencing、席位短锁、worker-6）已由大管家席位于 2026-11-02 14:13 至 14:25 轮次取得，本批不得推翻第一批结论，如有冲突以第一批为准并注明。" >> /home/ubuntu/ai-team/state/l3-v2/final-audit/seat-b-decision-loop2.log 2>&1; then
    echo "$(date "+%F %T") seat_run_done" >> /home/ubuntu/ai-team/state/l3-v2/final-audit/seat-b-decision-loop2.log
    break
  fi
  sleep 30
done
echo "$(date "+%F %T") loop_end" >> /home/ubuntu/ai-team/state/l3-v2/final-audit/seat-b-decision-loop2.log
