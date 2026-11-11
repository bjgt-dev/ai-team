---
name: cloud-worker
description: 云端薄工人接入与防呆：Muse VM 只跑确定性脚本（run_cycle v1.7 无状态版），严禁装 Agent 框架/本地模型；flock 防重入、原子认领防抢单、真实回执、模型只走 newapi 免费池。触发词：薄工人、工人接入、新工人、run_cycle、bootstrap、工人部署、worker。
---

# cloud-worker（云端薄工人接入与防呆）

## 核心理念
工人机器是无情的代码执行器：脑子在枢纽（脚本逻辑）+ newapi（模型）。工人越笨，集群越稳。
依据：2026-10-20 董事长定调——Worker 机严禁安装任何 Agent 框架（Claude Code/OpenCode/DSH）和本地模型；VPS 上重型执行容器曾空转烧 CPU，已于当日 14:05 全部停用。

## 前置检查（缺一不可，枢纽可重发）
1. `~/.ssh/muse_ingest` —— ingest 私钥（枢纽队列读写，600）。
2. `~/ai-worker/newapi.key` —— newapi 免费池 key（600，只留这一把）。
3. 发放点：枢纽 `/home/ubuntu/shared/worker-kit/`（工人用自己原有的 ubuntu 登录 scp 取回）。
4. 本机命令齐备：python3 / rsync / ssh / flock / timeout。缺任何一个先报卡点，不许自己装重型替代品。

## 执行步骤（接入流程）
1. 取凭据：从枢纽 shared/worker-kit/ 取 muse_ingest 与 newapi.key，chmod 600。
2. 拉三件套：枢纽 `ai-team/muse-worker/` 的 run_cycle.sh、worker_exec.py、gen_evidence.py。
3. 装机自检：`WORKER_ID=<guangtou|noesis> bash bootstrap_worker.sh`，必须看到 HUB_OK 与 BOOTSTRAP DONE。
4. 常驻（二选一）：tmux 循环（云端 VM 首选）`while true; do WORKER_ID=<id> bash run_cycle.sh >> cycle.log 2>&1; sleep <120 主力|600 备用>; done`；或 bootstrap 写入的 crontab。
5. 回执：在枢纽 `/home/ubuntu/shared/twt2026-inbox/` 写 `worker-up-<id>.md`（hostname、时间、tmux ls 输出）。

## run_cycle 硬规格（v1.7 无状态版）
- 临时目录干活（mktemp -d），trap EXIT 回传后自毁；本地只留脚本 + 两把钥匙，全部可重发。
- `flock -n` 防本机重入；认领走枢纽侧原子 `mv`（pending/muse → claimed/<WORKER_ID>/），防多工人抢单。
- 只拉自己认领的契约；rsync **严禁** `--remove-source-files`（防误删枢纽文件）。
- 执行走 worker_exec.py 按 task_type 分发；模型只调 newapi（executor-primary 免费池），**严禁**调用宿主/本地 Muse 模型与额度。
- 每轮先从枢纽自更新脚本，枢纽是唯一事实源。

## 证据纪律
- receipts.jsonl 由 worker_exec 执行时真实写入（tool_name/status/output_sha256/created_at 四字段 + 产物 write_file 回执）。
- gen_evidence.py 只生成 manifest.json 与 acceptance_self_check.json，**严禁伪造、补造 receipts**。

## 避坑警告
- 自检全过 ≠ 门禁通过：模拟自检全 PASS 的工人，真门禁首验曾被 REJECT（回执缺 write_file 条目）。以 verify.py 裁决为准。
- Cue 教训：执行沙箱因验证码死锁、SSH 端口焊死无法远程抢救——所以本地永不留唯一资产，机器随死随换，5 分钟 bootstrap 接管。
- 手搓证据包格式三连败（NO_VERDICT）：证据文件一律用 gen_evidence.py 生成，不许手写。

## 验证标准
- bootstrap 输出 BOOTSTRAP DONE 且 HUB_OK。
- 首轮 cycle 日志出现 claimed 或 idle，且枢纽 claimed/<WORKER_ID>/ 与 done/ 流转正常。
- 首个真实契约证据包过 verify.py 裁决 PASS。

## 信誉积分（2026-10-20 董事长令）
- 统计口径：以枢纽 logs/verify-*.json 裁决 + scan_inbox 完整性为准，台账 state/worker_reputation.md，TWT 维护。
- 过件率 = PASS / (PASS+REJECT+INCOMPLETE)。样本 <5 单只记数不评级。
- 分级派单：过件率 ≥90% 可接高价值任务；70–90% 常规任务；<70% 只派免费池脏活与 canary 测试单，先修稳定性再谈价值。
- INCOMPLETE（证据包不全、无裁决）与 REJECT 同等扣分：交不出完整证据 = 没干完。

## 避坑追加（错题本 · 2026-10-20）
- 早退路径必须写回执：worker_exec 在 "no input records" 等早退分支只写 FAILED、没落 receipts.jsonl，证据包永远 INCOMPLETE、门禁无从裁决（child-session-pipeline-001 实证）。任何退出路径先写 dispatch 回执再退。
