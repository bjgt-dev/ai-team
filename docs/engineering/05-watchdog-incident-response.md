# 规范05《异常巡检、告警与自愈预案》

> 状态：**[初版待验证]** —— 正文所引代码均已在枢纽实读，配套脚本已 `py_compile` + 自测通过，
> 但扫描 cron **未安装**、接管脚本 **未在枢纽实跑**、BLOCKED/TERMINAL_FAILED 落地状态 **待实现**。
> 本规范只新增“发现—告警—接管”的通路，不改 C2 执行器/核验器的状态机，不删任务、不改合同。

- 实测时间：2026-11-06 11:24–11:26（北京时间，枢纽 `CST`）
- 枢纽：`ssh arm-agent`，目录 `/home/ubuntu/ai-team`
- 配套脚本（与本文同一目录，scp 到枢纽后见第 8 节）：
  - `05-supervisor-stall-scan.py`（滞留扫描，只读库）
  - `05-hub-local-takeover.py`（hub-local 抢修接管触发器，默认 dry-run）

## 1. 真源（全部已在枢纽实读）

| 真源 | 实读结论 |
|---|---|
| `scripts/deadletter_watchdog.py` | v6：交单后 **10 分钟无 ACK** 才标死信（600 秒时间门禁），写 `ceo-seat/tasks/watchdog-retry-<trace8>.md` + `state/priority/ledger/WATCHDOG-DEADLETTER-<trace8>.json`，调 `hooks_wake.py` + `total_scheduler_event_bus.py`，记 `state/alerts.log` |
| `scripts/loop-watchdog.py` | `logs/butler-loop.log` 与最新 `state/eight-layer-review-*.json` **双双超 40 分钟**无更新且服务仍在，才判死锁；先调 `alert_push.sh DEADLOCK_LOOP_STALE` 推红警，再 `kill` loop 主 pid（systemd 30 秒重启） |
| `scripts/backup-watchdog.py` | 只在执行器级失败触发，**永不在质量 REJECT 上触发**：(a) `model-health route simple_query == 0` 持续 ≥30 分钟；(b) 最近 3 次不同执行器运行 `agent_exit != 0`。触发后 probe-first 自愈，失败则经 `hub_chat.py`（`glm-4-flash-cc` / `deepseek-chat`）做最小备工执行，写 deliverable + receipt；备工也失败则进决策队列并停（不循环）。护栏：flock 单例、每任务 24h 冷却、每次最多 1 个任务、永不碰 InstaBox 唤醒 |
| `scripts/health_3q-20261104.py` | 三问，exit 0/1/2/3：Q1 能用吗（butler synthetic DONE + 池 ≥2 模型 + C2 executor/verifier 日志 <10 分钟新鲜）；Q2 在做事吗（`central_tasks.updated_at` 60 分钟内有更新，或 24h 内有 DONE——注：C2 DSN 对 `central_events` 无 SELECT 权限，Q2 已改走 `central_tasks`，见代码注释 2026-11-04 fix）；Q3 对吗（最新 DONE 的 sha 抽检 + 待决队列 pending==0 + 根盘/数据盘 <85%）。exit 0 全过；1 为 Q1 过、Q2/Q3 部分；2 为 Q1 部分；3 为 Q1 失败 |
| `scripts/red_alert_triggers.py` | 每分钟 cron 跑，三硬阈值直推：A `GLM_STREAK_3`、D `NONZEN_STREAK_3`、B `QUEUE_OVER_20`（pending>20）、E `QUEUE_REPEAT_10`、C `NO_RECEIPT_30MIN`（dispatch-ack 超 30 分钟且之后无 revival-receipt，或 butler_loop.log 超 10 分钟无更新） |
| `scripts/alert_push.sh` | **TG 推送真实路径**（见第 5 节）：token/chat_id 从 `scripts/brain_watch.sh` 读取，`curl api.telegram.org/bot$TOKEN/sendMessage`；同一 KEY 60 分钟内只推一次（`FORCE=1` 可强制重推，测试用）；落盘 `state/red-alerts.log` + `state/alerts.log` |
| `scripts/queue_api-20261105.py` / `scripts/c2-verifier-20261104.py` / `scripts/c2-executor-20261104.py` | 状态机实查：失败 → `RETRY_WAIT`（`retry_count+1`，租约 RELEASED）；`retry_count >= max_retries` → `QUARANTINED`；`RETRY_WAIT` 须 `not_before` 已过才可重领（`cq-csv-20261102.py`）。**未在代码中找到 `BLOCKED` / `TERMINAL_FAILED` 状态的落地点**（见第 7 节待实现） |
| `scripts/cron_flock.sh` | cron 包装器：命名锁 + 8 个 slot 锁，抢不到锁记 `[SKIP]` 不静默跳过；每行调用格式见第 4 节 |
| 令19问4（任务书提供） | `RETRY_WAIT` 单次等待 ≤15 分钟、累计 ≤2 小时，超限进 `BLOCKED` / `TERMINAL_FAILED` + incident。**实查：executor/verifier 代码中未找到 `not_before` 的赋值与 15 分钟/2 小时上限的强制逻辑**（`not_before` 只在 `cq-csv` 的重领条件里被读）。本规范第 3 节的阈值由扫描脚本强制执行，但状态落库仍待实现（第 7 节） |

## 2. 事故实况：2026-11-06 11:10 三问 exit 2（任务书提供 + 部分实查复核）

任务书提供的实况：11:10 三问 exit 2，Q2 红、**5 小时 20 分无 DONE**、worker-1 自 10:10 起队列 API 静默约 1 小时、09:25 与 10:59 两度 `NO_RECEIPT_30MIN` 红警。

本次实查复核结果（2026-11-06 11:24–11:26）：

- ✅ `state/red-alerts.log` 确认两条 `NO_RECEIPT_30MIN`：
  - `2026-11-06 09:25:01 RED_ALERT NO_RECEIPT_30MIN 系统死锁红警：任务派发超 30 分钟无回执（派发 30 分钟前，回执 32 分钟前）`
  - `2026-11-06 10:59:02 RED_ALERT NO_RECEIPT_30MIN 系统死锁红警：任务派发超 30 分钟无回执（派发 30 分钟前，回执 31 分钟前）`
  - 同日志另有 `10:00/11:00 REDPOINT_TEST`（红点测试，非故障）与 `11:01 CEO_DOWNGRADE`（大管家降级用 `my-gateway/free-auto` 非 Zen）。
- ⚠️ **11:10 exit 2 的原始记录本次未复核到**：`state/status-board.md` 中 grep `exit` / `Q2` 无命中；11:10 的 Q2 红、5h20m 无 DONE、worker-1 10:10 起静默约 1 小时三项，**以任务书为准，未独立复核**，不得当作已验证事实引用。
- ✅ 同一时段 `red_alert_triggers.py` 最近一次运行输出为 `OK RECEIPT ack_age=1334 receipt_age=1378 loop_age=71`，`backup-watchdog.py` 最近 5 次均为 `{"status": "OK", "trigger": null, "route": 11}`——说明 11:24 时派发通路已恢复，不是持续死锁。

### 2.1 本次实查的现网滞留快照（2026-11-06 11:24，只读查询）

`central_tasks` 状态分布：DONE 523、**RETRY_WAIT 15、CLAIMED 8、READY 7**、QUARANTINED 10。
非终态中最旧的 10 条（`updated_at` 距今，UTC）：

| task_id（前 8 位） | status | retry_count / max_retries | 距上次更新 |
|---|---|---|---|
| `b639d483` | CLAIMED | 17 / 3 | 1 天 15 小时 |
| `f796300b` | CLAIMED | 18 / 3 | 1 天 8 小时 |
| `9ea61937` | CLAIMED | 18 / 3 | 1 天 1 小时 |
| `68fffa18` | CLAIMED | 17 / 3 | 1 天 0 小时 |
| `7fe85fcb` | RETRY_WAIT | 17 / 3 | 23 小时 |
| `e049bd8c` | RETRY_WAIT | 16 / 3 | 18 小时 |
| `4e168825` | CLAIMED | 14 / 3 | 16 小时 |
| `e00fc141` | RETRY_WAIT | 10 / 3 | 15 小时 |
| `76bbe7d7` | RETRY_WAIT | 4 / 3 | 13 小时 |
| `d5784373` | RETRY_WAIT | 3 / 3 | 13 小时 |

结论：**滞留不是假设，是现网正在发生的事实**——`retry_count` 已达 17–18，远超 `max_retries=3`，
任务仍以 CLAIMED/RETRY_WAIT 停留 13 小时以上、无人处置。这正是本规范要堵的口子：
旧口径下 `RETRY_WAIT` 只等 `not_before` 过期重领，无累计上限、无人盯累计时长。

## 3. 阈值表（扫描脚本强制执行）

状态名映射（任务书口径 → 库里实际 status，实查 2026-11-06）：

| 任务书口径 | `central_tasks.status` 实际值 | 黄（YELLOW） | 红（RED） | 升级动作 |
|---|---|---|---|---|
| PENDING | `READY` / `PENDING` / `DISPATCHING` | 停留 >30 分钟 | 停留 >60 分钟 | 红 → 推大管家（`alert_push.sh STALL_RED`，见第 5 节） |
| DOING | `CLAIMED` / `RUNNING` | 停留 >30 分钟 | 停留 >60 分钟 | 红 → 推大管家 |
| VERIFYING | `VERIFYING` | —（无黄） | 停留 >900 秒 | 红 → 推大管家 |
| RETRY_WAIT 单次 | `RETRY_WAIT`，`not_before - now` | — | 单次等待 >15 分钟 | 红 → 推大管家 |
| RETRY_WAIT 累计 | `RETRY_WAIT`，`now - updated_at` | — | — | 累计 >2 小时 → **incident**（写 `state/incidents/stall-<task_id>.json` + 推 `STALL_INCIDENT`；不改库，见第 7 节） |

说明：

- “停留时长”一律以 `central_tasks.updated_at` 起算（`now() - updated_at`），不以文件 mtime 推断；
  `not_before` 为 NULL 时单次等待判为未知（不误报，只看累计）。
- 黄只写 `state/alerts.log` + `state/stall-scan-latest.json`，**不推 TG**（防告警噪声；沿用
  `supervisor_check.py` “同一持续故障不重复”的口径）。
- 红/ incident 经 `alert_push.sh` 推 TG，同一 KEY 60 分钟去重（该脚本自带）。
- 本表不改变 deadletter（10 分钟无 ACK）、loop-watchdog（双日志 40 分钟）、
  backup-watchdog（路由空 30 分钟 / 执行器连败 3 次）、red_alert_triggers
  （`NO_RECEIPT_30MIN` 等）的既有阈值，只补“任务在库里滞留”这一层。

## 4. Supervisor 扫描脚本（Python，可运行）与 Cron 配置

### 4.1 脚本：`scripts/05-supervisor-stall-scan.py`

只读 `central_tasks`，永不改库；DSN 读 `state/.cq-dsn`（与 `health_3q` 同一来源）。
每分钟由 cron 跑，输出 `state/stall-scan-latest.json`（供第 6 节接管脚本复核用）。

```python
#!/usr/bin/env python3
"""05-supervisor-stall-scan: central_tasks 滞留扫描（规范05配套脚本，初版待验证）.

只读 central_tasks，永不改库。分类阈值（规范05第3节）：
  READY/PENDING、CLAIMED/RUNNING（DOING）: >30min 黄，>60min 红（推大管家）
  VERIFYING: >900s 红
  RETRY_WAIT: 单次 not_before 距 now >15min 违规；累计滞留（updated_at 起）>2h 写 incident
输出: state/stall-scan-latest.json；黄只写日志不推 TG；红调 scripts/alert_push.sh。
用法: python3 05-supervisor-stall-scan.py [--selftest] [--dry-run]
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

BASE = "/home/ubuntu/ai-team"
STATE = os.path.join(BASE, "state")
ALERT_PUSH = os.path.join(BASE, "scripts", "alert_push.sh")
OUT = os.path.join(STATE, "stall-scan-latest.json")
LOG = os.path.join(STATE, "alerts.log")

YELLOW_S = 30 * 60
RED_S = 60 * 60
VERIFYING_RED_S = 900
RETRY_SINGLE_MAX_S = 15 * 60
RETRY_CUMUL_MAX_S = 2 * 60 * 60

# 任务书口径 -> central_tasks 实际 status 的映射（实查 2026-11-06：库里只有
# READY/CLAIMED/RETRY_WAIT/DONE/QUARANTINED 等，无 PENDING/DOING/BLOCKED 行）
DOING = {"CLAIMED", "RUNNING"}
PENDING = {"READY", "PENDING", "DISPATCHING"}


def classify(status, age_s, retry_wait_not_before_s=None):
    """返回 (level, reason)：level in OK/YELLOW/RED/INCIDENT。"""
    if status in PENDING or status in DOING:
        if age_s > RED_S:
            return ("RED", "%s滞留%.0f分钟>60" % (status, age_s / 60))
        if age_s > YELLOW_S:
            return ("YELLOW", "%s滞留%.0f分钟>30" % (status, age_s / 60))
        return ("OK", "")
    if status == "VERIFYING":
        if age_s > VERIFYING_RED_S:
            return ("RED", "VERIFYING滞留%.0fs>900" % age_s)
        return ("OK", "")
    if status == "RETRY_WAIT":
        if retry_wait_not_before_s is not None and retry_wait_not_before_s > RETRY_SINGLE_MAX_S:
            return ("RED", "RETRY_WAIT单次等待%.0f分钟>15" % (retry_wait_not_before_s / 60))
        if age_s > RETRY_CUMUL_MAX_S:
            return ("INCIDENT", "RETRY_WAIT累计滞留%.1f小时>2" % (age_s / 3600))
        return ("OK", "")
    return ("OK", "")


def selftest():
    cases = [
        ("READY", 31 * 60, None, "YELLOW"),
        ("CLAIMED", 61 * 60, None, "RED"),
        ("RUNNING", 20 * 60, None, "OK"),
        ("VERIFYING", 901, None, "RED"),
        ("VERIFYING", 600, None, "OK"),
        ("RETRY_WAIT", 60 * 60, 16 * 60, "RED"),
        ("RETRY_WAIT", 3 * 3600, 5 * 60, "INCIDENT"),
        ("DONE", 999999, None, "OK"),
    ]
    bad = 0
    for st, age, nb, want in cases:
        got, reason = classify(st, age, nb)
        ok = got == want
        bad += 0 if ok else 1
        print("%s %-11s age=%s nb=%s -> %s (%s) want=%s" % (
            "PASS" if ok else "FAIL", st, age, nb, got, reason, want))
    print("SELFTEST %s (%d/%d)" % ("OK" if bad == 0 else "FAIL", len(cases) - bad, len(cases)))
    return 0 if bad == 0 else 1


def dsn():
    for line in open(os.path.join(STATE, ".cq-dsn")):
        line = line.strip()
        if line.startswith("CQ_DSN="):
            return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    return ""


def main():
    if "--selftest" in sys.argv:
        return selftest()
    dry_run = "--dry-run" in sys.argv
    import psycopg
    now = time.time()
    rows = []
    try:
        with psycopg.connect(dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT task_id, status, retry_count, max_retries,"
                    " EXTRACT(EPOCH FROM (now()-updated_at)),"
                    " EXTRACT(EPOCH FROM (not_before-now()))"
                    " FROM central_tasks"
                    " WHERE status NOT IN ('DONE','QUARANTINED')"
                    " ORDER BY updated_at")
                rows = cur.fetchall()
    except Exception as e:
        print(json.dumps({"stall_scan": "DB_ERROR", "err": str(e)[:200]}, ensure_ascii=False))
        return 2
    items, reds, incidents = [], [], []
    for tid, st, rc, mr, age_s, nb_s in rows:
        age_s = float(age_s or 0)
        nb_s = float(nb_s) if nb_s is not None else None
        level, reason = classify(st, age_s, nb_s)
        item = {"task_id": str(tid), "status": st, "retry_count": rc,
                "max_retries": mr, "age_s": int(age_s),
                "not_before_in_s": int(nb_s) if nb_s is not None else None,
                "level": level, "reason": reason}
        items.append(item)
        if level == "RED":
            reds.append(item)
        if level == "INCIDENT":
            incidents.append(item)
    out = {"ts": datetime.now(timezone.utc).astimezone().isoformat(),
           "scanned": len(items),
           "red": len(reds), "incident": len(incidents),
           "levels": {lv: sum(1 for i in items if i["level"] == lv)
                      for lv in ("OK", "YELLOW", "RED", "INCIDENT")},
           "items": [i for i in items if i["level"] != "OK"]}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("%s stall-scan scanned=%d yellow=%d red=%d incident=%d\n" % (
            out["ts"], out["scanned"], out["levels"]["YELLOW"],
            out["levels"]["RED"], out["levels"]["INCIDENT"]))
    if dry_run:
        print(json.dumps(out, ensure_ascii=False))
        return 0
    for it in reds:
        subprocess.run([ALERT_PUSH, "STALL_RED",
                        "任务滞留红警：%s %s，%s，请大管家立即处理。" % (
                            it["task_id"][:8], it["status"], it["reason"])],
                       capture_output=True, text=True, timeout=60)
    for it in incidents:
        subprocess.run([ALERT_PUSH, "STALL_INCIDENT",
                        "任务滞留 incident：%s %s，%s，已写 incident 草稿，需人工定案。" % (
                            it["task_id"][:8], it["status"], it["reason"])],
                       capture_output=True, text=True, timeout=60)
        idir = os.path.join(STATE, "incidents")
        os.makedirs(idir, exist_ok=True)
        with open(os.path.join(idir, "stall-%s.json" % it["task_id"]), "w",
                  encoding="utf-8") as f:
            json.dump({"tool": "05-supervisor-stall-scan", "kind": "RETRY_WAIT_OVER_2H",
                       "task_id": it["task_id"], "status": it["status"],
                       "retry_count": it["retry_count"], "max_retries": it["max_retries"],
                       "age_s": it["age_s"], "ts": out["ts"],
                       "action": "NEEDS_DECISION: BLOCKED/TERMINAL_FAILED 尚未在代码中落地，"
                                 "本稿只登记不改库，待人工定案"},
                      f, ensure_ascii=False, indent=2)
    print(json.dumps({"stall_scan": "OK", "scanned": out["scanned"],
                      "red": len(reds), "incident": len(incidents)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

实测：`python3 -m py_compile` 通过；`--selftest` 8/8 通过（READY 31min→YELLOW、
CLAIMED 61min→RED、RUNNING 20min→OK、VERIFYING 901s→RED、VERIFYING 600s→OK、
RETRY_WAIT 单次 16min→RED、RETRY_WAIT 累计 3h→INCIDENT、DONE→OK）。

### 4.2 Cron 配置（草稿，未安装）

> ⚠️ **草稿，未安装**——本行只写进规范，未写入枢纽 crontab。安装前须先在枢纽跑
> `--dry-run` 一轮、确认 `state/stall-scan-latest.json` 与日志正常，再由有 crontab
> 权限的人安装；安装后 10 分钟内复核一次日志。
> 格式照实现有 `cron_flock.sh`（命名锁 + 8 slot，抢不到记 `[SKIP]`），与现有
> `red_alert_triggers`（每分钟）、`deadletter_watch`（每 5 分钟）同格式：

```
* * * * * /home/ubuntu/ai-team/scripts/cron_flock.sh stall_scan python3 /home/ubuntu/ai-team/scripts/05-supervisor-stall-scan.py >> /home/ubuntu/ai-team/logs/stall-scan.log 2>&1
```

安装后核验（枢纽上跑）：

```bash
crontab -l | grep stall_scan
tail -5 /home/ubuntu/ai-team/logs/stall-scan.log
cat /home/ubuntu/ai-team/state/stall-scan-latest.json
```

## 5. TG 告警推送代码（真实路径已查到）

推送通道就是枢纽现成的 `scripts/alert_push.sh`（loop-watchdog 与 red_alert_triggers
已在用，不是新接线）。实读内容要点：

- 用法：`alert_push.sh <KEY> "<消息>"`；token/chat_id 从 `scripts/brain_watch.sh`
  读取（不打印、不外泄）；`curl -s -m 15 -X POST "https://api.telegram.org/bot$TOKEN/sendMessage"
  -d "chat_id=$CHAT" -d "text=🔴 $MSG"`。
- 同一 KEY 60 分钟内只推一次（`state/.red-alert-dedup.json`），`FORCE=1` 可强制重推（测试用）。
- 每次推送落盘 `state/red-alerts.log` + `state/alerts.log`，供审计。
- 无 token/chat_id 时输出 `TG_PUSH <KEY> NO_CHANNEL`，不静默假装已推。

本规范使用的 KEY（与既有 KEY 不冲突）：

| KEY | 触发方 | 含义 |
|---|---|---|
| `STALL_RED` | 05 扫描脚本 | PENDING/DOING >60min、VERIFYING >900s、RETRY_WAIT 单次 >15min |
| `STALL_INCIDENT` | 05 扫描脚本 | RETRY_WAIT 累计 >2h，已写 incident 草稿 |
| `NEEDS_DECISION` | 接管脚本 / backup-watchdog | 备工接管失败，需人工处理（backup-watchdog 已在用同一 KEY） |

调用示例（扫描脚本内已实现，见 4.1）：

```python
subprocess.run([ALERT_PUSH, "STALL_RED",
                "任务滞留红警：%s %s，%s，请大管家立即处理。" % (
                    task_id[:8], status, reason)],
               capture_output=True, text=True, timeout=60)
```

备用通知通道（backup-watchdog 已在用，实查存在）：`ceo-seat/work/notify.py push
--type BACKUP_TRIGGERED --task-id <id> --summary "<文本>"`。TG 直推失败时不降级
为“只写日志不推”——`alert_push.sh` 本身会在无通道时输出 `NO_CHANNEL`，由 cron
日志留痕，下次巡检可见。

## 6. hub-local 抢修工接管触发脚本

### 6.1 触发条件（须同时满足，缺一不接管）

1. `state/stall-scan-latest.json` 中该任务为 RED，且本脚本二次直读
   `central_tasks` 复核仍为 RED（防扫描报告过期导致的误抢）；
2. 该任务在 `central_leases` 中无 `ACTIVE` 租约——**有活租约绝不抢**；
3. 同一 task_id 24 小时内未接管过（读 `state/backup-watchdog-state.json` 的 `triggered`）；
4. 默认 dry-run：只写 `state/takeover-latest.json` 计划，不动作；真实执行须显式传 `--execute`。

### 6.2 动作（不自造执行器，复用 backup-watchdog 已有路径）

1. 跑 `backup-watchdog.py --dry-run` 探针；
2. 实跑 `backup-watchdog.py`：probe-first 自愈 → 路由恢复则停（正常执行器 resume）→
   否则经 `hub_chat.py` 做最小备工执行，写 deliverable + receipt，`notify.py` 推送
   `BACKUP_TRIGGERED`；
3. 备工失败 → `alert_push.sh NEEDS_DECISION` + 写 `state/incidents/takeover-<task_id>.json`，
   停住不循环。

### 6.3 回滚

- 本脚本**不删合同、不改任务状态**（状态流转仍由 C2 executor/verifier 走）；
- 若备工写出的 deliverable/receipt 有误，按 `state/takeover-latest.json` 记录的路径
  删除当次写出的两个文件即回滚，任务回到接管前状态，由人工重派；
- 24h 冷却内重复触发直接跳过，不循环抢；`flock` 单例（`/tmp/hub-local-takeover.lock`）。

### 6.4 脚本全文：`scripts/05-hub-local-takeover.py`

```python
#!/usr/bin/env python3
"""05-hub-local-takeover: hub-local 抢修工接管触发器（规范05配套脚本，初版待验证）.

只接管 stall-scan 已判 RED 且复核仍红的任务；默认 --dry-run，只写计划不动作。
真实执行须显式传 --execute。
条件（须同时满足）：
  1. state/stall-scan-latest.json 中该 task_id 为 RED（DOING>60min 或 VERIFYING>900s
     或 RETRY_WAIT 单次等待>15min），且本脚本二次直读 central_tasks 复核仍 RED；
  2. 该任务无 ACTIVE 租约（central_leases），或租约已过期——有活租约绝不抢；
  3. 同一 task_id 24 小时内未接管过（state/backup-watchdog-state.json 的 triggered）。
动作（按 backup-watchdog.py 已有路径，不自造执行器）：
  1. 先跑 backup-watchdog.py --dry-run 探针；2. 再实跑 backup-watchdog.py
     （probe-first 自愈 -> hub_chat 备工执行 -> 写 deliverable + receipt -> notify 推送）；
  3. 备工失败 -> alert_push.sh NEEDS_DECISION + state/incidents/takeover-<tid>.json。
回滚：本脚本不删合同、不改任务状态（状态流转仍由 C2 executor/verifier 走）；
  若备工写出的 deliverable/receipt 有误，按 state/takeover-latest.json 记录的
  path 删除当次写出的两个文件即回滚，任务回到接管前状态，由人工重派。
  24h 冷却内重复触发直接跳过，不循环抢。
用法: python3 05-hub-local-takeover.py [--execute] [--task <task_id>]
"""
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

BASE = "/home/ubuntu/ai-team"
STATE = os.path.join(BASE, "state")
SCRIPTS = os.path.join(BASE, "scripts")
STALL = os.path.join(STATE, "stall-scan-latest.json")
BW_STATE = os.path.join(STATE, "backup-watchdog-state.json")
LOCK = "/tmp/hub-local-takeover.lock"
COOLDOWN_S = 86400

_spec = importlib.util.spec_from_file_location(
    "stall_scan", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "05-supervisor-stall-scan.py"))
_mod = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(_mod)
    classify, dsn = _mod.classify, _mod.dsn
except Exception:
    classify = dsn = None


def log(msg):
    print(json.dumps({"takeover": msg}, ensure_ascii=False))


def main():
    execute = "--execute" in sys.argv
    task_arg = None
    if "--task" in sys.argv:
        task_arg = sys.argv[sys.argv.index("--task") + 1]
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        log("BUSY lock held")
        return 2
    try:
        if classify is None:
            log("ERROR cannot import supervisor_stall_scan (classify/dsn)")
            return 2
        try:
            scan = json.load(open(STALL, encoding="utf-8"))
        except Exception as e:
            log("ERROR no stall-scan report: %s" % e)
            return 2
        reds = [i for i in scan.get("items", []) if i.get("level") == "RED"]
        if task_arg:
            reds = [i for i in reds if i["task_id"] == task_arg]
        if not reds:
            log("OK no RED task to take over")
            return 0
        import psycopg
        with psycopg.connect(dsn()) as conn:
            with conn.cursor() as cur:
                for it in reds:
                    tid = it["task_id"]
                    cur.execute(
                        "SELECT status, EXTRACT(EPOCH FROM (now()-updated_at)),"
                        " EXTRACT(EPOCH FROM (not_before-now()))"
                        " FROM central_tasks WHERE task_id=%s", (tid,))
                    row = cur.fetchone()
                    if not row:
                        log("SKIP %s not in central_tasks" % tid[:8])
                        continue
                    st, age_s, nb_s = row
                    level, reason = classify(
                        st, float(age_s or 0),
                        float(nb_s) if nb_s is not None else None)
                    if level != "RED":
                        log("SKIP %s recheck now %s (%s)" % (tid[:8], level, reason))
                        continue
                    cur.execute(
                        "SELECT COUNT(*) FROM central_leases WHERE task_id=%s"
                        " AND status='ACTIVE'", (tid,))
                    if cur.fetchone()[0] > 0:
                        log("SKIP %s has ACTIVE lease, never抢" % tid[:8])
                        continue
                    try:
                        bw = json.load(open(BW_STATE, encoding="utf-8"))
                        last = bw.get("triggered", {}).get(tid, 0)
                    except Exception:
                        last = 0
                    if time.time() - last < COOLDOWN_S:
                        log("SKIP %s cooldown 24h" % tid[:8])
                        continue
                    plan = {"task_id": tid, "status": st, "reason": reason,
                            "ts": datetime.now(timezone.utc).astimezone().isoformat(),
                            "execute": execute}
                    with open(os.path.join(STATE, "takeover-latest.json"), "w",
                              encoding="utf-8") as f:
                        json.dump(plan, f, ensure_ascii=False, indent=2)
                    if not execute:
                        log("DRY-RUN plan %s" % json.dumps(plan, ensure_ascii=False))
                        continue
                    r = subprocess.run(
                        [sys.executable, os.path.join(SCRIPTS, "backup-watchdog.py")],
                        capture_output=True, text=True, timeout=900)
                    log("backup-watchdog rc=%s out=%s" % (r.returncode, (r.stdout or "")[:300]))
                    if r.returncode != 0:
                        subprocess.run(
                            [os.path.join(SCRIPTS, "alert_push.sh"), "NEEDS_DECISION",
                             "hub-local 接管失败：%s，备工亦失败，需人工处理。" % tid[:8]],
                            capture_output=True, text=True, timeout=60)
                        idir = os.path.join(STATE, "incidents")
                        os.makedirs(idir, exist_ok=True)
                        with open(os.path.join(idir, "takeover-%s.json" % tid), "w",
                                  encoding="utf-8") as f:
                            json.dump({"tool": "05-hub-local-takeover", "task_id": tid,
                                       "reason": reason, "ts": plan["ts"],
                                       "action": "NEEDS_DECISION"}, f, ensure_ascii=False)
        log("OK done execute=%s" % execute)
        return 0
    finally:
        try:
            os.unlink(LOCK)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
```

实测：`python3 -m py_compile` 通过；本地空跑输出
`{"takeover": "ERROR no stall-scan report: [Errno 2] No such file or directory:
'/home/ubuntu/ai-team/state/stall-scan-latest.json'"}`——fail-safe 行为正确：
无扫描报告即停手、不动作。**未在枢纽实跑**（需先有 stall-scan 报告 + `--execute`
显式授权），接管链路的端到端实跑待验证。

## 7. “任务卡 200 分钟无人知”的解法：触发链

旧链路的缺口（2026-11-06 实况）：任务在 `CLAIMED`/`RETRY_WAIT` 停 13–40 小时，
`retry_count` 冲到 17–18，系统无任何“累计滞留”告警——deadletter 只看“交单后
10 分钟无 ACK”，`NO_RECEIPT_30MIN` 只看“派发后 30 分钟无回执”，两者都不看
“任务已在库里躺了多久”。三问 Q2 只看“60 分钟内有无更新 / 24h 内有无 DONE”，
一个 DONE 就能把 Q2 顶成通过，掩盖个别任务的长期滞留。

本规范落地后，下次同类滞留的第一条消息是系统告警，触发链如下（按时间顺序）：

```
T+0     任务进入 READY/CLAIMED/RUNNING/VERIFYING/RETRY_WAIT（updated_at 开始计时）
T+10min 无 ACK → deadletter_watchdog 标死信、重发（既有，每 5 分钟跑）
T+30min 无回执 → red_alert_triggers NO_RECEIPT_30MIN 推 TG 红警（既有，每分钟跑）
T+30min 任务仍停留 → ★05 扫描判 YELLOW，写 state/alerts.log + stall-scan-latest.json（不推 TG）
T+60min 任务仍停留 → ★05 扫描判 RED，alert_push.sh STALL_RED 推 TG（“请大管家立即处理”）
        VERIFYING 更早：T+900s 即 RED
        RETRY_WAIT 单次等待 >15min 即 RED；累计 >2h 即 INCIDENT + incident 草稿
T+60min+  接管脚本（人工或定时触发，默认 dry-run）二次复核仍 RED 且无活租约
        → 调 backup-watchdog：probe-first 自愈 → 备工执行 → 失败则 NEEDS_DECISION + incident
```

关键点：**第一条面向人的消息不再依赖“有人恰好去查三问”**——T+30min 的
`NO_RECEIPT_30MIN`（既有）与 T+60min 的 `STALL_RED`（新增）都是 cron 自动推 TG，
且 `STALL_RED` 直接点名任务 id 与滞留原因。200 分钟的静默在 T+30/T+60 两道
关口上就已被打破；到 T+200min 时，该任务至少已触发 1 次黄、多次红、1 次
incident 草稿（RETRY_WAIT 累计 >2h），不可能“无人知”。

## 8. 真实运行记录（2026-11-06 11:24–11:26，枢纽实跑）

### 8.1 `health_3q-20261104.py` 实跑（exit 0，已恢复）

```bash
$ python3 /home/ubuntu/ai-team/scripts/health_3q-20261104.py; echo "EXIT=$?"
{"ts": "2026-11-06 11:24:28", "exit_code": 0, "supplement": {"ts": "2026-11-06 11:24:28", "metrics": {"gateway_token_call": {"ok": true, "model_count": 41}, "pg_connections": {"max": 100, "used": 9, "ratio": "9/100", "ratio_pct": 9.0}, "container_restarts": {"total": 12, "baseline": 12, "increment": 0, "per_container": {"beszel-agent-local": 0, "beszel-hub": 0, "foreman-shim": 0, "openclaw-official-openclaw-gateway-1": 0, "uptime-kuma": 0, "vaultwarden": 0, "ops-pg": 0, "compose-postgres": 0, "sub2api": 0, "sub2api-postgres": 0, "sub2api-redis": 0, "ai-stack-new-api-db-1": 0, "ai-stack-new-api-1": 12, "ai-stack-new-api-redis-1": 0}}, "inode": {"/": "6%", "/data": "11%"}, "backup": {"latest_date": "2026-11-06", "days_present": 10, "age_h": 3.4, "dates": ["2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]}, "queue_oldest_ready_h": 5.4, "lease_reclaim": {"total": 1568, "reclaimed": 1560, "rate": "1560/1568", "rate_pct": 99.5}, "decision_queue": {"pending_count": 0, "oldest_age_h": null, "note": "pending items carry no timestamp in current file format; age not fabricated"}}, "checks": {"gateway_token_ok": true, "pg_ratio_ok": true, "restart_inc_ok": true, "inode_ok": true, "backup_age_ok": true, "ready_age_ok": true, "decision_pending_ok": true, "lease_rate_ok": true}, "all_pass": true, "dry_run": true}, "supplement_pass": true, "q1_usable": true, "q2_working": true, "q3_correct": true, "q1": [["butler_synthetic", true], ["pool_models", 2], ["c2_executor_fresh", true], ["c2_verifier_fresh", true]], "q2": [["events_60min", 0], ["done_24h", 264]], "q3": [["pending_decisions", 0], ["done_sha_spotcheck", true], ["disk_root_pct", 28.8], ["disk_root_used_total_gb", "24/82"], ["disk_data_pct", 40.5]], "notes": [], "disk_touchline_note": "trend not yet built; <85% with no disk alert reported as pass"}
EXIT=0
```

解读：11:24 三问已回 exit 0（Q1/Q2/Q3 全过，24h 内 DONE 264），与 11:10 exit 2
（任务书）相比系统已恢复；但 `queue_oldest_ready_h: 5.4` 与第 2.1 节的滞留快照
说明“整体在做事”与“个别任务长期滞留”可以同时成立——三问过关 ≠ 无滞留，
这正是 05 扫描存在的理由。

### 8.2 真源脚本 `py_compile`（枢纽实跑）

```bash
$ python3 -m py_compile /home/ubuntu/ai-team/scripts/health_3q-20261104.py \
    /home/ubuntu/ai-team/scripts/deadletter_watchdog.py \
    /home/ubuntu/ai-team/scripts/loop-watchdog.py \
    /home/ubuntu/ai-team/scripts/backup-watchdog.py \
    /home/ubuntu/ai-team/scripts/red_alert_triggers.py && echo HUB_PY_COMPILE_OK
HUB_PY_COMPILE_OK
```

### 8.3 配套脚本自测（本地实跑）

- `05-supervisor-stall-scan.py`：`py_compile` 通过；`--selftest` 8/8 通过（见 4.1）。
- `05-hub-local-takeover.py`：`py_compile` 通过；空跑 fail-safe 正确（无扫描报告即停手，见 6.4）。

## 9. 待办（未实测 / 待实现，不算完成）

1. **Cron 未安装**：4.2 的 `stall_scan` 行是草稿。待办：枢纽 `--dry-run` 一轮 →
   确认 `stall-scan-latest.json` → 安装 → 10 分钟内复核日志。
2. **接管端到端未实跑**：`05-hub-local-takeover.py --execute` 未在枢纽跑过。
   待办：先有一次真实 RED 报告 → dry-run 看计划 → 显式授权后 `--execute` 一次 →
   核对 deliverable + receipt + 通知。
3. **`BLOCKED` / `TERMINAL_FAILED` 未落地**：令19问4 的终端状态在 `c2-*` /
   `queue_api` 代码中无落地点；`RETRY_WAIT` 单次 ≤15min / 累计 ≤2h 的上限
   在执行器/核验器中也无强制逻辑（`not_before` 只被读、未见赋值）。
   现状：05 扫描只做到“发现 + 告警 + incident 草稿”，**不改库**。待办：由 C2
   状态机负责人定状态落库方案（新增状态值 + 迁移 + `RETRY_WAIT` 累计计时字段），
   再改本规范第 3、7 节的措辞。
4. **11:10 exit 2 原始记录未复核**：第 2 节三项细节（Q2 红、5h20m 无 DONE、
   worker-1 10:10 起静默约 1 小时）以任务书为准；待办：找到当时的三问输出或
   状态板记录补链，补上后方可当作已验证事实引用。
5. **现网滞留任务本身未处置**：第 2.1 节的 8 条 CLAIMED / 15 条 RETRY_WAIT
   （最旧 1 天 15 小时，retry_count 17–18）是本次实查的发现，不属于本规范
   的交付范围；待办：按第 7 节触发链人工复核一次，逐条定去留（重派 / 废弃 /
   进 incident），不得以“规范已写”当作“任务已清”。
