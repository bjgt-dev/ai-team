# C4 失败账本入账检查清单（2026-11-10 董事长执行令）

## 谁在什么时候写
- 触发点（三者任一即入账）：任务进入 QUARANTINED；重试耗尽；verifier 拒收。
- 执行人：当班处置席（twt/复核席），在同一处置动作内入账，不得事后补记无证据条目。
- 工具：`scripts/business-failure-log-append.sh`（校验后写入）；只追加、不改历史行。

## 入账前逐项勾选
- [ ] task_id 是中央队列的真实 task_id（无队列任务的失败可空，但须在 evidence 写清来源）。
- [ ] 业务写清是哪条业务线/哪个任务，不是“系统”“队列”这类笼统词。
- [ ] 失败步骤写清在哪个步骤失败（如：执行、验收、派发、评审）。
- [ ] 直接原因是一句话，不写“失败了”“有问题”这类空话，不写推测。
- [ ] 类别只选一个：1 任务过大或模糊；2 模型能力不足；3 需求或验收标准不清；4 基建故障。
- [ ] 证据是日志行或收据 id，可复核（如 `state/logs/c2-executor.log: …`、`central_events …→QUARANTINED <时间>`、receipt id）。
- [ ] 不是系统自造任务（金丝雀、门禁测试等）——这类不入账。
- [ ] is_fixed 默认为 false；只有修后复测通过才写 true，且 retest_result 必须写复测结果（库 CHECK 约束强制，无复测写已修会被拒）。

## 入账后核对
- [ ] 脚本回显 failure_id，记下它（修基建的唯一例外凭证要引用该 id）。
- [ ] `SELECT * FROM business_failure_log WHERE failure_id=<id>;` 复核一行无误。

## 基建冻结例外口径
- 只有账本里归类为“4 基建故障”、且有 failure_id 和证据的条目，才允许修基建，且只修该故障本身。
- 类别 1/2/3 的失败不触发基建维修，走任务拆分/换模型/澄清需求。

## 例
```bash
scripts/business-failure-log-append.sh \
  --task-id 07dbfda7-1e62-4d7a-b76c-db482e25a34a \
  --business "枢纽注册表验证" \
  --failed-step "执行（worker-1 认领后执行）" \
  --direct-cause "worker-1 执行腿断：c2-executor 转交后 worker-1 认领即释放、零 checkpoint、零产物，三次重试后隔离。" \
  --category 4 \
  --evidence "state/logs/c2-executor.log: code deferred to worker-1 07dbfda7（×2）；central_events RETRY_WAIT→QUARANTINED 2026-11-10 04:50:02Z"
```
