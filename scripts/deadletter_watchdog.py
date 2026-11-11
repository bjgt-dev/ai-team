#!/usr/bin/env python3
# 物理看门狗 v6：加时间门禁，只有交单后超过 10 分钟仍无 ACK 才标死信，否则跳过。
import json, os, time
from datetime import datetime, timezone
BASE="/home/ubuntu/ai-team"
ROUTER=os.path.join(BASE,"logs/channel-router.jsonl")
ACK=os.path.join(BASE,"state/dispatch-ack.log")
TASKS=os.path.join(BASE,"ceo-seat/tasks")
LEDGER=os.path.join(BASE,"state/priority/ledger")
ALERTS=os.path.join(BASE,"state/alerts.log")
CUTOFF="2026-10-25T09:00"
def parse_ts(s):
    try:
        return datetime.fromisoformat(s).timestamp()
    except:
        return 0
def main():
    os.makedirs(LEDGER, exist_ok=True)
    ack_text=""
    try: ack_text=open(ACK).read()
    except: pass
    try: lines=open(ROUTER).readlines()
    except: return
    now=time.time()
    for line in lines:
        try: r=json.loads(line)
        except: continue
        if r.get("forward_status")!=200: continue
        ts=r.get("ts","")
        if ts < CUTOFF: continue
        tid=r.get("trace_id","")
        if not tid: continue
        # 时间门禁：交单时间距今不足 600 秒，跳过
        if now - parse_ts(ts) < 600: continue
        if tid in ack_text: continue  # per-trace, not global keyword
        fn=os.path.join(TASKS,f"watchdog-retry-{tid[:8]}.md")
        if not os.path.exists(fn):
            with open(fn,"w") as f:
                f.write(f"# 看门狗自动重发：交单后 10 分钟无 ACK\ntrace_id={tid} ts={ts}\n要求：立刻补回执票据，含工单号、状态、回执四字段或失败码、证据路径。缺格式按门禁打回。\n")
            with open(os.path.join(LEDGER,f"WATCHDOG-DEADLETTER-{tid[:8]}.json"),"w") as f:
                json.dump({"task_id":tid,"status":"deadletter","red_flag":True,"checked_at":datetime.now().astimezone().isoformat(),"source":"deadletter_watchdog.py v3 time-gate"},f,ensure_ascii=False)
            try:
                import subprocess
                subprocess.run(["python3","/home/ubuntu/ai-team/scripts/hooks_wake.py",tid], check=False)
                subprocess.run(["python3","/home/ubuntu/ai-team/scripts/total_scheduler_event_bus.py"], check=False)
            except Exception as e:
                print("auto wake call failed:", e)
            with open(ALERTS,"a") as f:
                f.write(f"{datetime.now().astimezone().isoformat()} deadletter watchdog retry trace={tid}\n")
if __name__=="__main__": main()
