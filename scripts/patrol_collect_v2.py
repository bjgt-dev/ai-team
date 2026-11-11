#!/usr/bin/env python3
# patrol_collect_v2.py -- deterministic patrol fallback layer (2026-10-28)
# No model dependency. Collects real state and atomically writes patrol-latest.
# Usage: patrol_collect_v2.py [--base DIR] [--out FILE] [--sort-log FILE]
#        [--mode MODE] [--note TEXT]
# Defaults are production paths. Staging tests MUST pass --base/--out/--sort-log
# pointing at a staging tree so fake state never touches production files.
# Exit 0 only if the output file was written and re-read with all required fields.
import argparse, json, os, shutil, subprocess, sys, time

REQ_FIELDS = ["patrol_ts", "queue", "gateway", "disk", "patrol_age", "ack_age"]


def count_files(d):
    try:
        n = 0
        for _, _, fs in os.walk(d):
            n += len(fs)
        return n
    except Exception:
        return -1


def sh(cmd, timeout=15):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True,
                              text=True, timeout=timeout).stdout.strip()
    except Exception:
        return "?"


def disk_line(path):
    try:
        u = shutil.disk_usage(path)
        pct = int(u.used * 100 / u.total) if u.total else -1
        return f"{path} {u.free // (1024**3)}G可用({u.used // (1024**3)}G/{u.total // (1024**3)}G, {pct}%已用)"
    except Exception:
        return f"{path} ?"


def file_age(path):
    try:
        return int(time.time() - os.path.getmtime(path))
    except Exception:
        return -1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="/home/ubuntu/ai-team")
    ap.add_argument("--out", default=None)
    ap.add_argument("--sort-log", default=None)
    ap.add_argument("--mode", default="collect")
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    base = a.base
    out = a.out or os.path.join(base, "ceo-seat/out/patrol-latest.md")
    sort_log = a.sort_log  # None => do not append (staging default safe)
    now = time.strftime("%F %T")
    now_iso = time.strftime("%FT%T+08:00")

    pend = {w: count_files(os.path.join(base, "contracts/pending", w))
            for w in ("cue", "guangtou", "muse", "noesis")}
    claim = count_files(os.path.join(base, "contracts/claimed"))
    pend_total = sum(v for v in pend.values() if v > 0)
    tasks = count_files(os.path.join(base, "ceo-seat/tasks"))
    alerts = []
    try:
        qa = json.load(open(os.path.join(base, "state/queue_alerts.json")))
        alerts = qa if isinstance(qa, list) else qa.get("alerts", [])
    except Exception:
        pass
    gw = sh("docker ps --format '{{.Names}} {{.Status}}' | grep -i openclaw-gateway | head -1")
    if not gw or gw == "?":
        gw = sh("sudo -n docker ps --format '{{.Names}} {{.Status}}' | grep -i openclaw-gateway | head -1")
    if not gw:
        gw = "?"
    disk = f"{disk_line('/')}；{disk_line('/data')}"
    latest = os.path.join(base, "ceo-seat/out/patrol-latest.md")
    # When --out is a staging file, age refers to the staging target's predecessor:
    # report the production file age only when writing the production file.
    age_src = latest if out == latest else out
    patrol_age = file_age(age_src)
    ack_age = file_age(os.path.join(base, "state/dispatch-ack.log"))
    brain = sh(f"tail -1 {base}/state/brain-active.log") or "?"
    cron = sh("crontab -l 2>/dev/null | grep -i patrol_run | head -1")
    top3 = []
    try:
        sl = sort_log or os.path.join(base, "state/priority/sort-log.jsonl")
        lines = open(sl).read().strip().split("\n")
        for ln in reversed(lines):
            if not ln.strip():
                continue
            o = json.loads(ln)
            if o.get("top3"):
                top3 = o["top3"]
                break
    except Exception:
        pass
    note = a.note or ("model round did not refresh patrol-latest; deterministic layer wrote this file"
                      if a.mode == "fallback-after-model" else
                      "deterministic layer; model round is optional supplement")
    report = [
        f"# 巡航 {now}（{a.mode}）",
        f"- 队列：pending 合计 {pend_total}（{json.dumps(pend, ensure_ascii=False)}）、claimed {claim}；告警 {len(alerts)} 条；ceo-seat/tasks {tasks} 项。",
        f"- 网关：{gw}；磁盘 {disk}。",
        f"- patrol-latest 前值年龄 {patrol_age}s；dispatch-ack 年龄 {ack_age}s；脑：{brain}。",
        f"- cron：{cron or '?'}。",
        f"- 排序：沿用上轮 top3（模型判断待模型轮次覆盖）：{json.dumps(top3, ensure_ascii=False)[:200]}。",
        f"- 说明：{note}。",
    ]
    text = "\n".join(report) + "\n"
    # Required-field self-check before writing.
    checks = [now in text, "pending" in text, gw != "", "磁盘" in text,
              str(patrol_age) in text, str(ack_age) in text]
    if not all(checks):
        print("COLLECT_FAIL self-check", checks, file=sys.stderr)
        return 2
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if os.path.exists(out):
        old = open(out).read()
        if old != text:
            ts = time.strftime("%Y%m%d-%H%M%S")
            shutil.copy2(out, f"{out}.bak-{ts}")
    tmp = f"{out}.tmp-{os.getpid()}"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, out)
    # Re-read verification: file exists, fresh, fields present.
    back = open(out).read()
    if back != text or file_age(out) > 60:
        print("COLLECT_FAIL re-read verification", file=sys.stderr)
        return 3
    if sort_log:
        entry = {"ts": now_iso, "mode": a.mode, "top3": top3, "note": note}
        with open(sort_log, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print("COLLECT_OK", now, a.mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
