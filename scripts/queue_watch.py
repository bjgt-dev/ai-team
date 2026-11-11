#!/usr/bin/env python3
# queue_watch.py — 队列活性看门狗（枢纽端，crontab 每分钟）
# 规则（2026-10-20 董事长令）：
#  - pending/ 中契约停留 > 3 分钟无人认领 -> UNCLAIMED 告警（工人疑似被平台休眠）
#  - claimed/ 中契约 > 15 分钟仍无证据包回传 -> STUCK：自动重投 pending（上限 2 次）并告警
# 告警落点：state/alerts.log（追加）+ state/queue_alerts.json（活动告警清单，供 TWT 本地盯守读取转发）
import json, os, subprocess, time
from datetime import datetime, timezone

BASE = "/home/ubuntu/ai-team"
PENDING = os.path.join(BASE, "contracts/pending/muse")
CLAIMED = os.path.join(BASE, "contracts/claimed")
INBOX = os.path.join(BASE, "inbox")
STATE_F = os.path.join(BASE, "state/queue_watch_state.json")
ALERTS_F = os.path.join(BASE, "state/queue_alerts.json")
LOG_F = os.path.join(BASE, "state/alerts.log")
UNCLAIMED_S = 180
STALLED_S = 1800
STUCK_S = 900
MAX_REQUEUE = 2


def move_file(src, dst):
    try:
        os.replace(src, dst)
    except PermissionError:
        # claimed/ files are ingest-owned; the watcher runs as ubuntu.
        # Passwordless sudo is the sanctioned path on the hub.
        subprocess.run(["sudo", "-n", "mv", src, dst], check=True)

def iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat(timespec="seconds")

def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)

def contract_created_at(path):
    try:
        with open(path) as f:
            c = json.load(f)
        ca = c.get("created_at")
        if ca:
            return datetime.fromisoformat(ca.replace("Z", "+00:00")).timestamp()
    except Exception:
        pass
    return os.stat(path).st_mtime

def main():
    now = time.time()
    state = load_json(STATE_F, {})
    active, events = [], []

    if os.path.isdir(PENDING):
        for fn in sorted(os.listdir(PENDING)):
            if not fn.endswith(".json"):
                continue
            path = os.path.join(PENDING, fn)
            task_id = fn[:-5]
            st = os.stat(path)
            created = contract_created_at(path)
            age = now - created
            if age > UNCLAIMED_S:
                active.append({"task_id": task_id, "kind": "UNCLAIMED",
                               "age_s": int(age), "since": iso(created),
                               "detail": "pending 无人认领 %d 分钟" % int(age // 60)})
                ep_key = "%s:unclaimed:%d" % (task_id, int(st.st_mtime))
                if state.get(ep_key) != "alerted":
                    state[ep_key] = "alerted"
                    events.append("UNCLAIMED %s age=%ds" % (task_id, int(age)))
            if age > STALLED_S:
                active.append({"task_id": task_id, "kind": "STALLED",
                               "age_s": int(age), "since": iso(created),
                               "detail": "pending 无人认领超 30 分钟，保活机制疑似整体失效，需人工唤醒"})
                skey = "%s:stalled:%d" % (task_id, int(st.st_mtime))
                if state.get(skey) != "alerted":
                    state[skey] = "alerted"
                    events.append("STALLED %s age=%ds" % (task_id, int(age)))

    if os.path.isdir(CLAIMED):
        for worker in sorted(os.listdir(CLAIMED)):
            wdir = os.path.join(CLAIMED, worker)
            if not os.path.isdir(wdir):
                continue
            for fn in sorted(os.listdir(wdir)):
                if not fn.endswith(".json"):
                    continue
                path = os.path.join(wdir, fn)
                task_id = fn[:-5]
                if os.path.isdir(os.path.join(INBOX, task_id)):
                    continue
                age = now - os.stat(path).st_ctime
                lease = None
                try:
                    lease = json.load(open(os.path.join(BASE, "contracts", "leases", task_id + ".json")))
                except Exception:
                    lease = None
                if lease:
                    # Supervisor rule (2026-10-20): reap ONLY when the heartbeat is
                    # dead AND the lease has expired. A live heartbeat protects the
                    # task no matter how long it has been running.
                    stuck = (now > float(lease.get("lease_expires_at", 0))) and \
                            (now - float(lease.get("heartbeat_at", 0)) > 120)
                else:
                    stuck = age > STUCK_S
                if stuck:
                    rq = int(state.get("%s:requeues" % task_id, 0))
                    if rq < MAX_REQUEUE:
                        try:
                            dest_dir = os.path.join(BASE, "contracts/pending", worker)
                            os.makedirs(dest_dir, exist_ok=True)
                            move_file(path, os.path.join(dest_dir, fn))
                            state["%s:requeues" % task_id] = rq + 1
                            events.append("STUCK_REQUEUE %s worker=%s age=%ds requeue#%d" % (task_id, worker, int(age), rq + 1))
                            active.append({"task_id": task_id, "kind": "STUCK_REQUEUED",
                                           "age_s": int(age), "since": iso(now),
                                           "detail": "%s 认领后 %d 分钟无回传，已重投 pending（第 %d 次）" % (worker, int(age // 60), rq + 1)})
                        except Exception as e:
                            events.append("STUCK_REQUEUE_FAIL %s %s" % (task_id, e))
                    else:
                        active.append({"task_id": task_id, "kind": "STUCK_FINAL",
                                       "age_s": int(age), "since": iso(now),
                                       "detail": "%s 认领后 %d 分钟无回传且已重投 %d 次，停止重投" % (worker, int(age // 60), rq)})
                        key = "%s:stuck_final" % task_id
                        if state.get(key) != "alerted":
                            state[key] = "alerted"
                            events.append("STUCK_FINAL %s worker=%s age=%ds" % (task_id, worker, int(age)))

    # PM 网关 RSS 守卫（2026-10-21 会诊新增：只告警，绝不自动重启）
    try:
        out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}",
                              "openclaw-official-openclaw-gateway-1"],
                             capture_output=True, text=True, timeout=25).stdout.strip()
        used = out.split("/")[0].strip()
        gib = 0.0
        if used.endswith("GiB"):
            gib = float(used[:-3])
        elif used.endswith("MiB"):
            gib = float(used[:-3]) / 1024.0
        if gib > 2.0:
            active.append({"task_id": "pm:gateway", "kind": "PM_RSS_HIGH",
                           "age_s": 0, "since": iso(now),
                           "detail": "网关 RSS %.2fGiB 超 2.0GiB 告警线（容器上限 3GiB，2026-10-21 裁决后口径）" % gib})
            hkey = "pm_rss:%d" % int(now // 3600)
            if state.get(hkey) != "alerted":
                state[hkey] = "alerted"
                events.append("PM_RSS_HIGH %.2fGiB" % gib)
    except Exception:
        pass

    save_json(STATE_F, state)
    hb_dir = os.path.join(BASE, "state", "heartbeats")
    if os.path.isdir(hb_dir):
        for fn in sorted(os.listdir(hb_dir)):
            if not fn.endswith(".json"):
                continue
            try:
                hb = json.load(open(os.path.join(hb_dir, fn)))
                silent_s = now - float(hb.get("ts", 0))
            except Exception:
                continue
            if silent_s > 720:
                active.append({"task_id": "worker:" + fn[:-5], "kind": "WORKER_SILENT",
                               "age_s": int(silent_s), "since": iso(now),
                               "detail": "%s heartbeat stopped %d min ago" % (fn[:-5], int(silent_s // 60))})
    save_json(ALERTS_F, {"updated_at": iso(now), "alerts": active})
    if events:
        with open(LOG_F, "a") as f:
            for e in events:
                f.write("%s queue_watch: %s\n" % (iso(now), e))
    print(json.dumps({"active_alerts": active, "events": events}, ensure_ascii=False))

if __name__ == "__main__":
    main()
