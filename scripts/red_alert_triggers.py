#!/usr/bin/env python3
# red_alert_triggers.py - 物理触发器（2026-10-31 反偷懒令，不靠主观判断）
# 每分钟由 cron 跑，三个硬阈值，命中即调 alert_push.sh 直推，不经审核、不总结：
#  A GLM_STREAK_3: brain-active.log 最近 3 条非巡航 brain= 全是 my-gateway/glm
#  B QUEUE_OVER_20: decision-queue.json pending > 20
#  C NO_RECEIPT_30MIN: 最近一次 dispatch-ack 超 30 分钟且之后无 revival-receipt，或 butler_loop.log 超 10 分钟无更新（死锁前兆）
# 去重在 alert_push.sh（60 分钟冷却，FORCE=1 强制）。
import json, os, re, subprocess, time
BASE = "/home/ubuntu/ai-team"
STATE = os.path.join(BASE, "state")
ALERT = os.path.join(BASE, "scripts", "alert_push.sh")

def push(key, msg):
    subprocess.run([ALERT, key, msg], capture_output=True, text=True, timeout=60)

def mtime(p):
    try:
        return os.path.getmtime(p)
    except Exception:
        return 0

def main():
    now = time.time()
    # A
    try:
        lines = open(os.path.join(STATE, "brain-active.log"), encoding="utf-8", errors="replace").read().splitlines()
        brains = []
        for ln in lines:
            if "patrol brain=" in ln:
                continue
            m = re.search(r"brain=(\S+)", ln)
            if m:
                brains.append(m.group(1))
        last3 = brains[-3:]
        if len(last3) == 3 and all(x.startswith("my-gateway/glm") for x in last3):
            push("GLM_STREAK_3", "系统死锁红警：连续 3 次弱模型兜底（glm），强脑 Zen 未接管，属重大违规，请立即查强脑直连。")
            print("FIRED GLM_STREAK_3", last3)
        else:
            print("OK GLM_STREAK last3=", last3)
    except Exception as e:
        print("ERR GLM", e)
    # D NONZEN_STREAK_3: last 3 non-patrol brains all gateway (not Zen) -- gateway order change made GLM_STREAK blind to gemini/free-auto fallback, so non-Zen must alarm on its own, dedup 60min in alert_push
    try:
        lines2 = open(os.path.join(STATE, "brain-active.log"), encoding="utf-8", errors="replace").read().splitlines()
        brains2 = []
        for ln in lines2:
            if "patrol brain=" in ln:
                continue
            m = re.search(r"brain=(\S+)", ln)
            if m:
                brains2.append(m.group(1))
        last3n = brains2[-3:]
        if len(last3n) == 3 and all(x.startswith("my-gateway/") for x in last3n):
            push("NONZEN_STREAK_3", "系统红警：连续 3 次非 Zen 脑兜底（网关模型），强脑 Zen 未接管，属长期弱模型兜底，请查 Zen 直连与家宽轮换。")
            print("FIRED NONZEN_STREAK_3", last3n)
        else:
            print("OK NONZEN_STREAK last3=", last3n)
    except Exception as e:
        print("ERR NONZEN", e)
    # B
    try:
        d = json.load(open(os.path.join(STATE, "decision-queue.json")))
        n = len(d.get("pending", []))
        if n > 20:
            push("QUEUE_OVER_20", f"系统死锁红警：待决队列 {n} 条，超过 20 条红线，请立即分流，勿再静默积压。")
            print("FIRED QUEUE_OVER_20", n)
        else:
            print("OK QUEUE", n)
    except Exception as e:
        print("ERR QUEUE", e)
    # E QUEUE_REPEAT_10: single pending item repeat_count>=10 (stuck decision, not queue size) -- pending=1 can still be stuck
    try:
        d2 = json.load(open(os.path.join(STATE, "decision-queue.json")))
        reps = [(p.get("id"), p.get("repeat_count", 0)) for p in d2.get("pending", []) if p.get("repeat_count", 0) >= 10]
        if reps:
            push("QUEUE_REPEAT_10", f"系统红警：待决单重复卡死 {reps}，决策席未定案，请立即定案或关单，勿再重复计数。")
            print("FIRED QUEUE_REPEAT_10", reps)
        else:
            print("OK QUEUE_REPEAT", reps)
    except Exception as e:
        print("ERR QUEUE_REPEAT", e)
    # C
    try:
        ack_age = None
        try:
            ack_lines = open(os.path.join(STATE, "dispatch-ack.log"), encoding="utf-8", errors="replace").read().splitlines()
            if ack_lines:
                ack_age = now - mtime(os.path.join(STATE, "dispatch-ack.log"))
        except Exception:
            pass
        loop_age = now - mtime(os.path.join(BASE, "logs", "butler-loop.log"))
        out_dir = os.path.join(BASE, "ceo-seat", "out")
        receipts = sorted([f for f in os.listdir(out_dir) if f.startswith("revival-receipt-")]) if os.path.isdir(out_dir) else []
        last_receipt_age = now - mtime(os.path.join(out_dir, receipts[-1])) if receipts else 999999
        if loop_age > 600:
            push("DEADLOCK_LOOP_STALE", f"系统死锁红警：大管家循环日志 {int(loop_age//60)} 分钟无更新，疑似假死，请立即查进程。")
            print("FIRED DEADLOCK_LOOP_STALE", int(loop_age))
        elif ack_age is not None and ack_age > 1800 and last_receipt_age > 1800:
            push("NO_RECEIPT_30MIN", f"系统死锁红警：任务派发超 30 分钟无回执（派发 {int(ack_age//60)} 分钟前，回执 {int(last_receipt_age//60)} 分钟前），请立即查通路。")
            print("FIRED NO_RECEIPT_30MIN", int(ack_age), int(last_receipt_age))
        else:
            print("OK RECEIPT ack_age=", int(ack_age) if ack_age else None, "receipt_age=", int(last_receipt_age), "loop_age=", int(loop_age))
    except Exception as e:
        print("ERR RECEIPT", e)

if __name__ == "__main__":
    main()
