#!/usr/bin/env python3
# loop-watchdog: biguan loop deadlock guard (2026-10-30 SRE self-check).
# Normal cycle writes log lines within 20 min; if log AND review file both stale over 40 min
# while service stays active, treat as deadlock (long sessions still update cycle log, no false kill).
# Action: kill only the loop main pid (owned by ubuntu, no sudo needed); systemd restarts in 30s.
# Sessions become orphans and survive; runner flock prevents double-run. Log only on action.
import os, subprocess, time, json, glob
BASE = "/home/ubuntu/ai-team"
LOG = os.path.join(BASE, "logs/butler-loop.log")
def _mtime(p):
    try:
        return os.path.getmtime(p)
    except Exception:
        return 0
def _main_pid():
    try:
        r = subprocess.run(["systemctl", "show", "butler-loop.service", "-p", "MainPID", "--value"], capture_output=True, text=True, timeout=15)
        return int((r.stdout or "0").strip() or 0)
    except Exception:
        return 0
def main():
    now = time.time()
    log_age = now - _mtime(LOG)
    rev_age = 10 ** 9
    try:
        fs = sorted(glob.glob(os.path.join(BASE, "state", "eight-layer-review-*.json")))
        if fs:
            rev_age = now - _mtime(fs[-1])
    except Exception:
        pass
    if log_age < 600 or rev_age < 600:
        return
    # 物理直连报警：先推红警，再杀进程；pid 未知也不静默返回
    try:
        subprocess.run(["/home/ubuntu/ai-team/scripts/alert_push.sh", "DEADLOCK_LOOP_STALE", "🔴 系统死锁：大管家循环日志 %d 秒、审查 %d 秒无更新，疑似假死，正在处理。" % (int(log_age), int(rev_age))], capture_output=True, text=True, timeout=60)
    except Exception:
        pass
    pid = _main_pid()
    if not pid:
        print(json.dumps({"loop_watchdog": "deadlock_alerted_no_pid", "log_age_s": int(log_age), "rev_age_s": int(rev_age)}))
        return
    try:
        os.kill(pid, 15)
        print(json.dumps({"loop_watchdog": "killed_deadlocked_loop", "pid": pid, "log_age_s": int(log_age), "rev_age_s": int(rev_age)}))
    except Exception as e:
        print(json.dumps({"loop_watchdog": "kill_failed", "err": str(e)[:200]}))
if __name__ == "__main__":
    main()
