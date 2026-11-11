# B 级定案书（汇编，2026-11-02）

## 汇编说明
- 第一批（fencing、席位短锁、worker-6）：大管家席位第 25 轮，来源 ceo-seat/out/model-output-20261102-141326-752919.txt，定案部分 sha256 见校验输出。
- 第二批（supervisor docstring、事件规则表、事件去重、发布后线上核实）：未取得定案，不得实施。
- 冲突注记：worker-6 目标状态值存在第二份席位定案冲突（UNVERIFIED vs DELISTED），实施前须复核确认；第二份定案所称回执 receipt-final-audit-20261102-144727.md 经核查不存在。

---

# B 级定案书（最终审计，2026-11-02）

**定案人**：大管家席位（COO+首席架构师+安全官）
**性质**：只定案、不实施、不修改文件。本文即定案书，不外发。
**脑通道如实注明**：Zen 自 2026-11-02 13:34 起 DEAD，本轮实际脑 = my-gateway 兜底（非 Zen），符合 b-decision-request 约束。

---

## 第一项：l3_claim.py 增加 fencing

### 定案结论：**修改（定案通过，按以下规格实施）**

### 定案规格

**新 CLI**：`l3_claim.py <task_id> <worker_id> <new_status> [claim_token] [artifact] [note]`（token 作第 4 位参数，在 artifact/note 之前，避免位置重排破坏既有 6 位调用）。

**校验规则（写入前）**：
1. `new_status == "CLAIMED"`：若当前任务 `claim_token is not None` 或 `status in ("CLAIMED","RUNNING")` → 拒绝重复领取，写审计行、exit 3，不落盘。否则生成新 token（保持现有 sha256(task_id:attempt:ts)[:16] 算法，兼容 supervisor 回收语义）。
2. `new_status in ("RUNNING","DONE","FAILED","VERIFYING")`：必须提供 `sys.argv[4]` 且等于该任务当前 `claim_token`，否则拒绝（审计+exit 4，不落盘）。`claim_token is None` 的任务（历史遗留）拒绝任何状态推进，除非带 `--legacy` 标志（仅允许一次性注记，不改 status 实质，留痕 `legacy_bypass=true`）。
3. 写前 flock 已由 supervisor.run_locked 持有（保持）；写盘保持 tmp+replace 原子写。
4. 拒绝时必须 append 审计文件 `state/l3-v2/claim-audit.jsonl`（task_id、old_token、given_token、caller_pid、ts、reason）。

**历史事实注明（禁止伪造）**：
- `L3-004-review3`：DONE，`claim_token` 为 None（queue.json 实测无该字段），attempt=0，created 13:14，updated 13:35:14——**从未走 CLAIM 路径直接置 DONE**。
- `L3-OP-UGC-001`：DONE，`claim_token` 为 None（queue.json 实测无该字段），attempt=1 但 token 为 None——**存在绕过 CLAIM 直接置 DONE 的路径**。
- 两条记录**不得**补写 token 值。实施后这两条作为 legacy 记录保留原样，新增字段 `legacy_token_null=true` 仅作注记，不填造值。

### 备份办法
- `cp -a /home/ubuntu/ai-team/state/l3-v2/l3_claim.py /home/ubuntu/ai-team/state/l3-v2/backup/l3_claim.py.bak-20261102-fencing`（带日期后缀，不覆盖已有 `.py.bak`）。
- 同时备份 queue.json：`cp /home/ubuntu/ai-team/state/l3-v2/queue.json /home/ubuntu/ai-team/state/l3-v2/backup/queue.json.bak-20261102-fencing`。
- 改前跑 `python3 -m py_compile` 确认新文件语法。

### 明确回滚办法
- `cp /home/ubuntu/ai-team/state/l3-v2/backup/l3_claim.py.bak-20261102-fencing /home/ubuntu/ai-team/state/l3-v2/l3_claim.py`（恢复原 24 行版本）。
- queue.json 无需回滚：fencing 校验只拒绝非法写入，合法 DONE 任务的 queue.json 内容不变；回滚后旧 token 仍留在任务条目中，不影响既有 DONE 记录。
- claim-audit.jsonl 为新增文件，回滚时直接删除即可（无生产依赖）。

### 2 小时实施窗口
| 时刻 | 动作 |
|---|---|
| T+0 | 备份 l3_claim.py + queue.json，py_compile 确认基线可编译 |
| T+5min | 实施 l3_claim.py fencing 修改，py_compile 通过 |
| T+15min | 沙箱实测：复制 l3_claim.py + 最小 queue.json 到 /tmp/fencing-test，跑 4 个用例（正常 CLAIM、正常 DONE、旧 token 覆写→拒绝、无 token 直接 DONE→拒绝），确认审计行生成 |
| T+30min | 生产切换：用 flock 包新 l3_claim.py 跑一条真实 PENDING→CLAIMED→DONE 用例，验证 queue.json 更新正确 |
| T+45min | 独立复核（见下） |
| T+60min | 确认 claim-audit.jsonl 无异常拒绝记录，supervisor cron 下一轮（每 5 分钟）正常跑通 |
| T+120min | 窗口关闭，出定案回执 |

### 实测办法
1. **正常路径**：PENDING→CLAIMED 生成 token，CLAIMED→DONE 带 token 成功。
2. **旧 token 覆写**：先 CLAIMED 拿到 token A，再用 token B（非当前）写 DONE → 必须 exit 4，queue.json 无变化，审计行 `stale_token_rejected` 生成。
3. **重复领取**：已 CLAIMED 任务再 CLAIMED → exit 3，queue.json 无变化。
4. **legacy 保护**：L3-OP-UGC-001（token None）尝试写 FAILED → 必须拒绝；带 `--legacy` 可注记但 status 不变。
5. **supervisor 回收兼容**：supervisor.py 回收逻辑（清空 claim_token）与新校验不冲突（回收后 token=None，后续状态推进须带 --legacy 或重新 CLAIM）。

### 独立复核办法
- 复核席：supervisor_worker（非执行席）。
- 复核内容：① 读 l3_claim.py 新代码，确认 4 条校验规则逐条存在；② 读 claim-audit.jsonl 最近 5 行，确认拒绝记录格式完整；③ 读 queue.json 中 L3-OP-UGC-001 与 L3-004-review3，确认 claim_token 仍为 None（未被伪造）；④ 跑 supervisor cron 一轮，确认无 `claim_token` 相关报错。
- 复核通过标准：4 项全过，出 `verify-fencing-20261102.json`（含 sha256 与时间戳）。

---

## 第二项：ceo-opencode.sh 席位锁架构重构

### 定案结论：**修改（定案通过，按以下规格实施）**

### 定案规格

**现行问题**：长会话持锁（flock 从开跑到关，`exec 9>/tmp/ceo-opencode.lock` 全程不释放），导致但ler_loop/seat_revive 在会话进行中无法抢占，优先标志 `priority-external-task` 形同虚设。

**新架构（短锁领单 + 独立执行 + 短锁回写）**：

```
阶段1 短锁领单（flock 持锁 < 2s）：
  - flock -n /tmp/ceo-opencode.lock
  - 读 priority-external-task 标志：若存在 → 写 claim 文件
    /tmp/ceo-opencode.claim.{pid}（含 pid、start_epoch、task_hash）
  - 释放 flock（close fd 9）
  - 若标志不存在 → 走常规巡航领单，同样写 claim 文件

阶段2 独立执行（不持锁）：
  - 跑 opencode run（最长 600s）
  - 不持有 /tmp/ceo-opencode.lock
  - 其他进程此时可获锁领下一单（但 claim 文件唯一性保证同一时刻只有一个执行中会话）

阶段3 短锁回写（flock 持锁 < 2s）：
  - 重新 flock /tmp/ceo-opencode.lock
  - 写回执（output_sha256、patrol 刷新等）
  - 更新 holder 文件
  - 释放 flock
```

**holder 文件（PID 重用防护）**：
- `/tmp/ceo-opencode.holder` 增加 `start_epoch` 字段（进程启动时 `date +%s` 捕获）。
- 校验逻辑：读 holder 文件时，用 `ps -o etimes= -p {pid}` 取实际运行时长；若 holder 中记录的 `start_epoch` 与 `now - etimes` 偏差 > 10s，判定 PID 已被重用，holder 失效，重新领单。
- 领单时：若 /tmp/ceo-opencode.claim.* 文件存在且对应 holder 的 PID 仍活跃（通过 /proc/{pid}/starttime 校验，而非仅 PID 存在）→ 拒绝领单（exit 1，reason=another_session_active）。

**抢占语义修正**：
- 优先标志 `priority-external-task` 存在时，新领单走优先路径；若已有活跃会话（claim 文件有效），**不杀旧会话**，而是等旧会话阶段3回写释放 claim 文件后，下一轮（seat_revive 每 5 分钟）自动抢占。
- 若旧会话已运行 > 30 分钟且 priority 标志已存在 > 15 分钟，**记死信推 B**（不杀，不等待，上报）。

**API key 处理**：`MY_GATEWAY_API_KEY` 硬编码在脚本第 5 行，属已知的低危缺陷（内网可读取），本轮**不在此项实施范围内**，单独记待决（C 级，需董事长定案是否迁移到 env 文件）。

### 备份办法
- `cp /home/ubuntu/ai-team/scripts/ceo-opencode.sh /home/ubuntu/ai-team/scripts/ceo-opencode.sh.bak-20261102-shortlock`（沿用现有 `.bak-*` 命名规范）。
- 同时备份 `butler_seat_revive.py`（其 `seat_lock_busy` 分支逻辑需适配新架构，但本轮只定案 ceo-opencode.sh，revive.py 的适配单独列为实施项 2b）。

### 明确回滚办法
- `cp /home/ubuntu/ai-team/scripts/ceo-opencode.sh.bak-20261102-shortlock /home/ubuntu/ai-team/scripts/ceo-opencode.sh`
- 回滚后：flock 长持锁行为恢复，优先标志失效（同现状），无数据丢失。
- claim 文件 `/tmp/ceo-opencode.claim.*` 为新增，回滚时直接 `rm -f /tmp/ceo-opencode.claim.*` 即可。

### 2 小时实施窗口
| 时刻 | 动作 |
|---|---|
| T+0 | 备份 ceo-opencode.sh，bash -n 确认基线语法 |
| T+10min | 实施短锁三段式修改 + holder start_epoch 校验，bash -n 通过 |
| T+25min | 沙箱：复制脚本到 /tmp，手动跑一次完整会话（短任务），确认 flock 释放后另一个进程能领单 |
| T+40min | PID 重用测试：手动写一个 start_epoch 偏差 > 10s 的 holder 文件，确认领单被拒绝 |
| T+55min | 生产切换（在 seat_revive 非活跃时段，避开 cron 5 分钟窗口） |
| T+70min | 独立复核（见下） |
| T+90min | 等一轮 seat_revive cron（5 分钟内），确认无 `seat_lock_busy` 误报 |
| T+120min | 窗口关闭，出定案回执 |

### 实测办法
1. **短锁释放**：领单阶段完成后 2s 内 `flock -n /tmp/ceo-opencode.lock echo ok` 必须成功（旧架构会失败）。
2. **独立执行无锁**：opencode run 进行中，`lsof /tmp/ceo-opencode.lock` 应无进程持有（只读检查）。
3. **holder PID 重用**：手动构造 holder（start_epoch = now - 7200，PID = 任意存在但不相关的进程），跑领单 → 必须拒绝。
4. **抢占等待**：priority-external-task 存在 + 活跃会话运行中 → 新领单不抢占、不杀旧，记审计行 `preempt_wait`。
5. **死信触发**：模拟 priority 标志存在 15 分钟 + 旧会话 > 30 分钟 → 推 B。

### 独立复核办法
- 复核席：hub-local（非执行席）。
- 复核内容：① 读新 ceo-opencode.sh，确认 flock 只在阶段1和阶段3持有；② 读 /tmp/ceo-opencode.holder（若存在），确认 start_epoch 字段格式；③ 跑 `bash -n` 确认语法；④ 读但ler_seat_revive 最近一次 cron 日志，确认无新 `seat_lock_busy` 误报（若已适配）或有（若未适配，记待决）；⑤ 读 claim-audit（若有）确认无异常拒绝。
- 通过标准：5 项全过，出 `verify-shortlock-20261102.json`。

---

## 第三项：workers.json 中 worker-6 条目处理

### 定案结论：**标注未验证 + 保留更正记录（三选一中的组合方案）**

### 定案依据
- 2026-11-02 13:44 InstaBox API 只读 GET 实测（证据：`evidence-inventory-machines-20261102.md`）：API 返回 5 台机器（worker-1 至 worker-5，machine_id 均与 workers.json 一致），**worker-6 不在返回列表中**。
- workers.json 中 worker-6 条目（machine_id `m_agqv3oeaqad7z75wlewoy47eg4`）`status: "UNKNOWN"`、`verified: false`、`in_pool: false`、`wakeable: null`、`last_failure: "not verified"`。
- 历史上 worker-6 曾被 registry 列入，但本次 API 实测未返回——**不能确证机器已下线**（API 可能分页、或有权限限制），也不能确证机器仍存在（本次 5 台全量返回 nextCursor=None）。

### 定案规格

**选择"标注未验证 + 保留更正记录"，不删除条目**：

1. worker-6 条目**保留**在 workers.json 中（不删除），更新以下字段：
   - `status: "UNVERIFIED"`（从 "UNKNOWN" 改，语义更明确）
   - `note: "2026-11-02 13:44 InstaBox API 只读实测未返回此机器，无法确证在线状态；保留条目不作删除，待后续 API 复查确认。"`
   - `verified: false`（保持）
   - `in_pool: false`（保持）
2. 在 `workers.json` 的 `note` 字段追加更正记录：
   ```
   "correction_20261102": "worker-6 (m_agqv3oeaqad7z75wlewoy47eg4) 2026-11-02 13:44 API 实测不在返回列表（5台：worker-1至worker-5），原 registry 中 worker-6 条目来源未标注，标记 UNVERIFIED，不作删除，待后续确认。"
   ```
3. **不删除历史**：原始条目保留，只改 status 与 note，不销毁 machine_id 与规格字段。

### 备份办法
- `cp /home/ubuntu/ai-team/state/l3-v2/workers.json /home/ubuntu/ai-team/state/l3-v2/backup/workers.json.bak-20261102-w6`
- 同时保留证据文件 `evidence-inventory-machines-20261102.md`（只读，不改）。

### 明确回滚办法
- `cp /home/ubuntu/ai-team/state/l3-v2/backup/workers.json.bak-20261102-w6 /home/ubuntu/ai-team/state/l3-v2/workers.json`
- 回滚后 worker-6 恢复 `status: "UNKNOWN"`，correction 字段消失，无数据丢失。

### 2 小时实施窗口
| 时刻 | 动作 |
|---|---|
| T+0 | 备份 workers.json |
| T+5min | 修改 worker-6 条目（status→UNVERIFIED、加 note、加 correction_20261102 字段），`python3 -c "import json; json.load(open(...))"` 确认 JSON 合法 |
| T+15min | 独立复核（见下） |
| T+30min | 确认 supervisor cron 下一轮正常读 workers.json（无报错） |
| T+45min | 窗口关闭，出定案回执（此项为轻量修改，无需等 2 小时满） |

### 实测办法
1. `python3 -c "import json; d=json.load(open('/home/ubuntu/ai-team/state/l3-v2/workers.json')); w6=[w for w in d['workers'] if w['worker_id']=='worker-6'][0]; print(w6['status'], w6.get('note',''))"` → 必须输出 `UNVERIFIED` + 正确 note 文本。
2. `grep "correction_20261102" /home/ubuntu/ai-team/state/l3-v2/workers.json` → 必须有匹配行。
3. `python3 -c "import json; json.load(open('/home/ubuntu/ai-team/state/l3-v2/workers.json'))"` → exit 0（JSON 合法）。
4. 确认 worker-6 的 machine_id 未被改动（与备份对比）。

### 独立复核办法
- 复核席：supervisor_worker。
- 复核内容：① 读新 workers.json，确认 worker-6 status 为 UNVERIFIED、note 与 correction 字段存在；② 对比备份文件，确认 machine_id 未变；③ 读 `evidence-inventory-machines-20261102.md`，确认"worker-6 不在 API 返回列表"的事实与 note 文字一致；④ 确认未删除其他 7 个 worker 条目。
- 通过标准：4 项全过，出 `verify-w6-20261102.json`。

---

## 附：未定案事项（超出本轮范围，记待决）

| 事项 | 级别 | 说明 |
|---|---|---|
| supervisor.py docstring 与代码不一致（aging/fencing/flock/heartbeat 四处） | B 级（b-decision-request 第 4 项，本轮未定案） | aging 与 fencing 属功能缺失，须单独定案是否补实现；flock 问题：main() 不持锁，并发竞写 queue.json |
| `MY_GATEWAY_API_KEY` 硬编码于 ceo-opencode.sh 第 5 行 | C 级（需董事长定案） | 内网可读取，建议迁移至 env 文件，但属对外配置变更，须董事长批 |
| butler_seat_revive.py 适配短锁架构（实施项 2b） | B 级 | 其 `seat_lock_busy` 分支在新架构下语义变化，须单独定案 |

---

*定案书完。本文为定案文件，不外发。实施须按上述备份→修改→沙箱→生产→复核顺序执行，任一环节失败即回滚，不得跳过复核。*


---

## 第二批状态：未取得定案
supervisor docstring、事件规则表、事件去重、发布后线上核实四项：第二批重试环 60 轮耗尽（2026-11-02 14:28-15:33），三次获得席位均超时只产出过程记录，未产出定案正文，故一律记“未定案”，不得实施，须另起席位轮次重新取得定案。
