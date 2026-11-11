import urllib.request, json, pathlib, datetime, subprocess, sys
def wake(tid):
    token = pathlib.Path('/home/ubuntu/ai-team/state/.hooks-token').read_text().strip()
    body = json.dumps({"message": "deadletter wake trace=" + tid, "agentId": "butler", "sessionKey": "agent:butler:channel", "name": "deadletter-watchdog", "idempotencyKey": "deadletter:" + tid, "deliver": False}).encode()
    req = urllib.request.Request("http://127.0.0.1:18789/hooks/agent", data=body, method="POST", headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = r.read(1000).decode(errors="replace")
            status = r.status
    except Exception as e:
        status = 0
        payload = type(e).__name__ + ": " + str(e)
    line = datetime.datetime.now().astimezone().isoformat() + " agent=butler sessionKey=agent:butler:channel trace=" + tid + " hooks_status=" + str(status) + " payload=" + payload[:200] + chr(10)
    pathlib.Path('/home/ubuntu/ai-team/logs/deadletter-hooks-wake.log').open('a').write(line)
    print("hooks_wake status=" + str(status))
    # 事件驱动第二版：唤醒即只做最新单，不跑全量巡航。flock 防重叠。
    subprocess.Popen(["flock", "-n", "/tmp/patrol-run.lock", "/home/ubuntu/ai-team/scripts/event_task.sh"], stdout=open("/home/ubuntu/ai-team/logs/event-patrol.log", "a"), stderr=subprocess.STDOUT, start_new_session=True)
    print("event_task started (single-task, strong model gpt-oss-120b)")
if __name__ == "__main__":
    wake(sys.argv[1] if len(sys.argv) > 1 else "test")
