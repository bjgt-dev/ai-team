#!/usr/bin/env python3
# 总调度事件总线：秘书角色，不是塞纸条。
# 输入格式只允许：新工单、死信报告、里程碑验收单；底层日志不推。
# 实现：扫描 ceo-seat/tasks/ 中符合上述三类的最新文件，生成一条高优事件文本，
# 由调用方通过大管家运行器注入其会话（运行器本身写 ACK 在先）。
# 本脚本只生成事件文本文件，不直接跑模型，避免 cron 轮次内重复唤醒。
import os, json, time
BASE="/home/ubuntu/ai-team"
TASKS=os.path.join(BASE,"ceo-seat/tasks")
OUT=os.path.join(BASE,"ceo-seat/out/event-bus-latest.md")
ALLOWED=("p1-","test-","urgent-","followup-","reject-","chagang-","watchdog-retry-","ack-retry-","realtime-","execution-order-")
def main():
    files=[]
    for f in os.listdir(TASKS):
        if f.startswith(ALLOWED) and f.endswith((".md",".json")):
            p=os.path.join(TASKS,f)
            files.append((os.path.getmtime(p),f))
    files.sort(reverse=True)
    top=files[:10]
    with open(OUT,"w") as out:
        out.write("# 总调度事件推送（高优待办事件）\n")
        out.write("优先级：绝对高于自主巡检，收到立刻挂起巡检，优先处理并回 ACK 票据。\n")
        out.write("输入格式：只含新工单、死信报告、里程碑验收单，不含底层日志。\n")
        for mt,f in top:
            out.write(f"- {f} mtime={int(mt)}\n")
        out.write("\n票据要求：工单号、状态、四字段或失败码、证据路径，无票据视为未执行。\n")
    print(f"event_bus wrote {OUT} top={len(top)}")
if __name__=="__main__": main()
