#!/usr/bin/env python3
import json
import os
import re
import subprocess
import time
from datetime import datetime

HUB = "/home/ubuntu/ai-team"
STATE = os.path.join(HUB, "state")
CONTAINER = "openclaw-official-openclaw-gateway-1"
SAMPLES = os.path.join(STATE, "gateway-rss-samples.jsonl")
LATEST = os.path.join(STATE, "gateway-rss-latest.json")
SEEN = os.path.join(STATE, "gw-rss-guard-seen.json")
HANDOFF = os.path.join(STATE, "handoff.md")
RESTART_LOG = os.path.join(STATE, "gateway-restart.log")
GIB = 1024 ** 3


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sh(cmd, timeout=25):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as e:
        return 1, "", type(e).__name__


def parse_gib(text):
    usage = (text or "").split("/", 1)[0]
    m = re.search(r"([0-9.]+)\s*(GiB|MiB)", usage)
    if not m:
        return None
    value = float(m.group(1))
    return value if m.group(2) == "GiB" else round(value / 1024.0, 3)


def inspect():
    fmt = "{{.State.Health.Status}}|{{.RestartCount}}|{{.State.StartedAt}}"
    rc, out, err = sh(["sudo", "-n", "docker", "inspect", "-f", fmt, CONTAINER])
    if rc != 0:
        return {"health": "UNKNOWN", "restart_count": None, "started_at": "", "error": err}
    parts = out.split("|")
    return {
        "health": parts[0] if len(parts) > 0 else "UNKNOWN",
        "restart_count": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None,
        "started_at": parts[2] if len(parts) > 2 else "",
        "error": "",
    }


def stats():
    rc, out, err = sh([
        "sudo", "-n", "docker", "stats", "--no-stream",
        "--format", "{{.MemUsage}}|{{.CPUPerc}}", CONTAINER])
    if rc != 0:
        return {"mem": "", "rss_gib": None, "cpu_pct": None, "error": err}
    parts = out.split("|")
    return {
        "mem": parts[0] if parts else "",
        "rss_gib": parse_gib(parts[0] if parts else ""),
        "cpu_pct": parts[1] if len(parts) > 1 else "",
        "error": "",
    }


def worker_count():
    rc, out, err = sh(["sudo", "-n", "docker", "top", CONTAINER, "-eo", "pid,comm"])
    if rc != 0:
        return None
    return max(0, len(out.splitlines()) - 1)


def contention_raw():
    rc, out, err = sh(["sudo", "-n", "docker", "logs", "--since", "5m", CONTAINER])
    if rc != 0:
        return None
    return out.count("GatewayStateOwnerContentionError")


def restart_reason():
    try:
        lines = open(RESTART_LOG, errors="ignore").read().splitlines()[-200:]
    except Exception:
        return "unknown"
    reason = "unknown"
    for line in lines:
        m = re.search(r"reason=([A-Za-z0-9_.-]+)", line)
        if m:
            reason = m.group(1)
    return reason


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


def append_handoff(title, lines):
    with open(HANDOFF, "a") as f:
        f.write("\n## %s %s\n" % (now_iso(), title))
        for line in lines:
            f.write("- %s\n" % line)


def main():
    ins = inspect()
    st = stats()
    sample = {
        "time": now_iso(),
        "health": ins.get("health"),
        "restart_count": ins.get("restart_count"),
        "started_at": ins.get("started_at"),
        "mem": st.get("mem"),
        "rss_gib": st.get("rss_gib"),
        "mem_limit_gib": 3.0,
        "cpu_pct": st.get("cpu_pct"),
        "os_processes": worker_count(),
        "workers": worker_count(),
        "workers_scope": "os-processes（非 JS worker 线程数）",
        "contention_raw_5m": contention_raw(),
        "contention_scope": "raw log lines; startup update-history warning may be included",
        "restart_reason": restart_reason(),
    }
    with open(SAMPLES, "a") as f:
        f.write(json.dumps(sample, ensure_ascii=False) + "\n")
    save_json(LATEST, sample)

    seen = load_json(SEEN, {})
    now = time.time()
    alerts = []

    rss = sample.get("rss_gib")
    if rss is not None and rss >= 2.5:
        key = "rss_crit"
        if now - float(seen.get(key, 0)) > 10 * 60:  # 2026-10-24 chairman: suppression 6h -> 10min
            seen[key] = now
            alerts.append(("GW_RSS_CRITICAL", [
                "rss=%.3fGiB >= 2.5GiB 硬闸；health=%s workers=%s" % (
                    rss, sample.get("health"), sample.get("workers")),
                "处置：查 TOP worker/锁事件；需人工评估是否走 gateway_ctl 受控重启。",
            ]))
    elif rss is not None and rss >= 2.0:
        key = "rss_warn"
        if now - float(seen.get(key, 0)) > 10 * 60:  # 2026-10-24 chairman: suppression 6h -> 10min
            seen[key] = now
            alerts.append(("GW_RSS_WARN", [
                "rss=%.3fGiB >= 2.0GiB 预警；health=%s workers=%s" % (
                    rss, sample.get("health"), sample.get("workers")),
                "仅记录，不进主聊；连续上升转人工。",
            ]))

    prev_started = seen.get("started_at")
    cur_started = sample.get("started_at")
    if prev_started and cur_started and cur_started != prev_started:
        key = "restart:" + cur_started
        if key not in seen:
            seen[key] = now
            alerts.append(("GW_RESTART", [
                "started_at=%s reason=%s restart_count=%s" % (
                    cur_started, sample.get("restart_reason"), sample.get("restart_count")),
                "计划内重启由 gateway_ctl.sh 记录；非计划重启按 P0-1 告警。",
            ]))
    if cur_started:
        seen["started_at"] = cur_started

    save_json(SEEN, seen)
    for title, lines in alerts:
        append_handoff(title, lines)

    print(json.dumps({"sample": sample, "alerts": [a[0] for a in alerts]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
