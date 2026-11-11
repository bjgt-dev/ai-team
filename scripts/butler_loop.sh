#!/bin/bash
# butler_loop: 大管家常驻监督循环（2026-10-30 架构归位，2026-10-30 晚八层+专家闭环升级）。
# 注意：常驻的是“监督循环”，不是单个不死的 agent 会话——每轮都经 ceo-opencode.sh 起一个全新会话，
# 旧会话不硬续（防上下文膨胀与幻觉），状态靠交接文件与落盘回执传递。运行器自带 flock，复活 cron 触发的
# 运行若在进行，本轮自动跳过，不冲突。systemd Restart=always 兜底：循环死了 30 秒内拉起。
# 每轮先跑八层审查代码（butler_eight_layer_review.py：磁盘、过期租约、fb 残留、写死扫描、路由健康），
# 审查有新发现时该脚本已按去重规则调过一次专家（Gemini），答案写进架构札记，会话负责消化执行。
TASK='常驻架构师当班：只读精简摘要 /home/ubuntu/ai-team/ceo-seat/out/butler-slim-summary.md 与最新交接文件，不读 259KB 架构札记全文、不读 59KB 决策队列全文；需要细节时再按任务号单条查。能自己动手且可逆、低风险的修（先备份、改后跑验收、不删数据、不关真实待决条目、不编造回执与任务号），直接修并写回执；审查有新发现且不可自动修复时，必须先调 /home/ubuntu/ai-team/scripts/web_brain.py（格式：真机情况 + 卡点 + 候选方案 A/B）问专家（Gemini 优先，DeepSeek 未登录时跳过），消化意见后再修或推决策队列；高危（磁盘清理后仍超 90%）立即推决策队列并同步问专家，不等下一轮；修不了的死结，推决策队列并带 A/B 方案与影响说明，等 Twt 转报董事长。如 ceo-seat/tasks/ 有未审的 review-*.md 定案请求，审阅该请求文件与所指材料后写 verdict（通过/有条件通过/打回，附条件与理由）到 state/butler-verdicts/<任务名>.json，一轮只审最早的一份。最后把本轮结论写成简短架构札记到 /home/ubuntu/ai-team/ceo-seat/out/butler-arch-note.md（只写本轮事实，不写状态行凑数；只追加、禁整文件覆盖，追加前先备份）。'
LOOP_FILE=/home/ubuntu/ai-team/scripts/butler_loop.sh
LOOP_HASH_START=$(sha256sum "$LOOP_FILE" | cut -d" " -f1)
while true; do
  if [ -f /home/ubuntu/ai-team/state/priority-external-task ]; then echo $(date '+%F %T') "[LOOP] priority-external-task present, skipping cycle" >> /home/ubuntu/ai-team/logs/butler-loop.log; sleep 60; continue; fi
  LOOP_HASH_NOW=$(sha256sum "$LOOP_FILE" | cut -d" " -f1)
  if [ "$LOOP_HASH_NOW" != "$LOOP_HASH_START" ]; then echo $(date '+%F %T') [HOT-RELOAD] loop file changed, exiting for systemd restart >> /home/ubuntu/ai-team/logs/butler-loop.log; exit 0; fi
  echo $(date '+%F %T') [LOOP] cycle start >> /home/ubuntu/ai-team/logs/butler-loop.log
  python3 /home/ubuntu/ai-team/scripts/butler_eight_layer_review.py >> /home/ubuntu/ai-team/logs/butler-loop.log 2>&1
  # 2026-10-30 大管家自驱：每轮顺带跑一次当日自查（脚本内按日期去重，当日已跑即退出，不重复打分）。
  python3 /home/ubuntu/ai-team/scripts/butler-self-check.py >> /home/ubuntu/ai-team/logs/butler-loop.log 2>&1
  timeout 120 python3 /home/ubuntu/ai-team/scripts/butler_redblue.py >> /home/ubuntu/ai-team/logs/butler-loop.log 2>&1 || true
  # 2026-10-30 第一性原理重构：监督者不阻塞等工人——会话脱离后台跑（自带锁防重入），循环只管监督，
  # 会话日志独立文件，循环周期缩到约3分钟；监督频率上去后，死锁看门阈值可从40分钟收紧到10分钟。
  ( /home/ubuntu/ai-team/scripts/ceo-opencode.sh "$TASK" >> /home/ubuntu/ai-team/logs/butler-session.log 2>&1 & )
  echo $(date '+%F %T') [LOOP] cycle done session-detached >> /home/ubuntu/ai-team/logs/butler-loop.log
  sleep 180
done
# HOTRELOAD-TEST2-20261031
