#!/usr/bin/env python3
"""Deterministic patrol (expert-corrected final, 2026-10-28).

Rules (do not change without a new final):
- This script ONLY collects read-only local state and writes
  ceo-seat/out/patrol-latest.md. It never overwrites that file with
  model output, and a model failure never fails the patrol run.
- Model output, when supplied via --model-file, is ONLY validated and,
  on success, written to ceo-seat/out/model_verdict.md. It never
  touches patrol-latest.md.
- Model BAD criteria: gateway 429 / unparseable / elapsed > 10s.
  A BAD result only records model_bad and resets the consecutive
  success counter to 0. It does not touch patrol-latest.md.
- Recovery criterion: 3 consecutive successful parses
  (state file model_consecutive_success.txt >= 3).
- Dead criterion: patrol file age > 300s before this run
  (reported as patrol_dead_before; this run cures it by rewriting).
- NEVER modifies: patrol_run.sh, ceo-opencode.sh, butler config,
  /opt/model-profiles expert login archives (no delete/move/chmod).
  On any captcha/verification challenge: stop and report, do not bypass.
- No business write commands are executed. Only this script's own
  outputs are written: patrol-latest.md, optionally model_verdict.md,
  and state files under state/patrol-deterministic-20261028/.
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone, timedelta

BASE = "/home/ubuntu/ai-team"
OUT = os.path.join(BASE, "ceo-seat/out/patrol-latest.md")
MODEL_OUT = os.path.join(BASE, "ceo-seat/out/model_verdict.md")
STATE_DIR = os.path.join(BASE, "state/patrol-deterministic-20261028")
SEATS = ["cue", "guangtou", "muse", "noesis", "main-worker"]
PORTS = [8090, 5432, 6080, 5900, 10814]
DEAD_SECONDS = 300  # patrol 自身超龄阈值；ack 判死用 DEAD_SECONDS_ACK
DEAD_SECONDS_ACK = 1800  # 2026-11-05 ARCHFIX: ack 真告警线 1800s；300s 误判为噪声
MODEL_TIMEOUT_SECONDS = 10
RECOVERY_STREAK = 3
CST = timezone(timedelta(hours=8))


def now_cst():
    return datetime.now(CST)


def fmt_ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def file_age_seconds(path, ref):
    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=CST)
        return int((ref - mtime).total_seconds()), fmt_ts(mtime)
    except OSError:
        return None, "missing"


def count_files(path):
    try:
        return sum(1 for e in os.scandir(path) if e.is_file())
    except OSError:
        return None


def count_entries(path):
    try:
        return sum(1 for e in os.scandir(path) if not e.name.startswith("."))
    except OSError:
        return None


def disk_line(path):
    try:
        u = shutil.disk_usage(path)
        total_g = u.total / (1024 ** 3)
        avail_g = u.free / (1024 ** 3)
        used_pct = 100.0 * (u.total - u.free) / u.total if u.total else 0.0
        return f"{path} {avail_g:.1f}G可用({used_pct:.0f}%已用,共{total_g:.0f}G)"
    except OSError as exc:
        return f"{path} unavailable ({exc})"


def port_status(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        rc = s.connect_ex(("127.0.0.1", port))
        return "open" if rc == 0 else f"closed(rc={rc})"
    except OSError as exc:
        return f"error({exc})"
    finally:
        s.close()


def docker_gateway_line():
    try:
        out = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}} {{.Status}}"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        for line in out.splitlines():
            if "openclaw" in line and "gateway" in line:
                return line.strip()
        return "gateway container line not found in docker ps"
    except Exception as exc:  # read-only probe failure must not fail patrol
        return f"docker ps unavailable ({exc})"


def atomic_write(path, text):
    tmp = path + ".tmp-deterministic"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def read_streak():
    p = os.path.join(STATE_DIR, "model_consecutive_success.txt")
    try:
        return int(open(p, encoding="utf-8").read().strip())
    except (OSError, ValueError):
        return 0


def write_state_json(status_obj):
    p = os.path.join(STATE_DIR, "model_status.json")
    atomic_write(p, json.dumps(status_obj, ensure_ascii=False, indent=2) + "\n")


def classify_model(text, elapsed_seconds, today):
    """Return (ok: bool, reason: str). BAD = 429 / unparseable / timeout."""
    if elapsed_seconds is not None and elapsed_seconds > MODEL_TIMEOUT_SECONDS:
        return False, f"timeout elapsed={elapsed_seconds}s>10s"
    low = text.lower()
    if "429" in text or "rate limit" in low or "too many requests" in low:
        return False, "gateway 429 / rate limit"
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines or len(lines) < 4:
        return False, f"unparseable: only {len(lines)} non-empty lines"
    if today not in lines[0]:
        return False, "unparseable: first line lacks today date"
    if "pending" not in text:
        return False, "unparseable: missing pending"
    return True, "parseable"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-file", default=None,
                    help="Optional model output file to VALIDATE only.")
    ap.add_argument("--model-elapsed", type=float, default=None,
                    help="Seconds the model round took (>10 => timeout BAD).")
    args = ap.parse_args()

    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    run_dt = now_cst()
    run_ts = fmt_ts(run_dt)
    today = run_dt.strftime("%Y-%m-%d")

    patrol_age_before, patrol_mtime_before = file_age_seconds(OUT, run_dt)
    ack_path = os.path.join(BASE, "state/dispatch-ack.log")
    ack_age, ack_mtime = file_age_seconds(ack_path, run_dt)
    ack_copy = os.path.join(BASE, "ceo-seat/out/dispatch-ack-copy.log")
    ack_copy_age, ack_copy_mtime = file_age_seconds(ack_copy, run_dt)

    pending = {}
    claimed = {}
    missing_dirs = []
    for seat in SEATS:
        for kind, dest in (("pending", pending), ("claimed", claimed)):
            d = os.path.join(BASE, "contracts", kind, seat)
            if not os.path.isdir(d):
                dest[seat] = 0
                missing_dirs.append(d)
            else:
                dest[seat] = count_files(d)
    pending_total = sum(v for v in pending.values() if v is not None)
    claimed_total = sum(v for v in claimed.values() if v is not None)
    tasks_n = count_entries(os.path.join(BASE, "ceo-seat/tasks"))
    alerts_path = os.path.join(BASE, "state/alerts.log")
    alerts_lines = None
    alerts_mtime = "missing"
    try:
        with open(alerts_path, encoding="utf-8", errors="replace") as f:
            alerts_lines = sum(1 for _ in f)
        alerts_mtime = fmt_ts(datetime.fromtimestamp(
            os.path.getmtime(alerts_path), tz=CST))
    except OSError:
        pass

    ports = {p: port_status(p) for p in PORTS}
    patrol_dead_before = (
        patrol_age_before is not None and patrol_age_before > DEAD_SECONDS)
    ack_dead = ack_age is not None and ack_age > DEAD_SECONDS_ACK

    # --- Model handling: NEVER touches patrol-latest.md. ---
    streak_before = read_streak()
    model_line = "model: not-invoked (deterministic-only run)"
    if args.model_file is not None:
        try:
            text = open(args.model_file, encoding="utf-8",
                        errors="replace").read()
        except OSError as exc:
            text = ""
            ok, reason = False, f"unparseable: cannot read model file ({exc})"
        else:
            ok, reason = classify_model(text, args.model_elapsed, today)
        if ok:
            streak = streak_before + 1
            recovered = streak >= RECOVERY_STREAK
            model_line = (f"model: ok streak={streak} "
                          f"recovered={'yes' if recovered else 'no'} "
                          f"(needs {RECOVERY_STREAK} consecutive)")
            atomic_write(os.path.join(STATE_DIR,
                                      "model_consecutive_success.txt"),
                         f"{streak}\n")
            atomic_write(MODEL_OUT,
                         f"# model_verdict {run_ts}\n"
                         f"- status: model_ok\n- reason: {reason}\n"
                         f"- consecutive_success: {streak}\n"
                         f"- recovered: {'yes' if recovered else 'no'}\n"
                         f"- source: {args.model_file} (validated only; "
                         f"patrol-latest.md untouched by model)\n")
            write_state_json({"ts": run_ts, "status": "model_ok",
                              "reason": reason,
                              "consecutive_success": streak,
                              "recovered": recovered})
        else:
            # BAD: only record model_bad, reset streak, never fail patrol.
            model_line = (f"model: model_bad reason={reason} "
                          f"streak reset 0 (patrol unaffected)")
            atomic_write(os.path.join(STATE_DIR,
                                      "model_consecutive_success.txt"), "0\n")
            atomic_write(MODEL_OUT,
                         f"# model_verdict {run_ts}\n"
                         f"- status: model_bad\n- reason: {reason}\n"
                         f"- consecutive_success: 0\n"
                         f"- recovered: no\n"
                         f"- note: model failure recorded only; "
                         f"patrol-latest.md NOT overwritten by model\n")
            write_state_json({"ts": run_ts, "status": "model_bad",
                              "reason": reason,
                              "consecutive_success": 0,
                              "recovered": False})
    # --- Deterministic patrol file (always written, model-independent). ---
    body = (
        f"# 巡航 {run_ts}（deterministic）\n"
        f"- 来源：确定性采集 {__file__}，模型不覆盖本文件；"
        f"模型 verdict 如有只写 model_verdict.md。\n"
        f"- 时间：本轮 {run_ts} CST；patrol 前值 mtime={patrol_mtime_before}，"
        f"前值年龄={patrol_age_before}s，"
        f"判死(>300s)={'yes' if patrol_dead_before else 'no'}。\n"
        f"- 队列：pending 合计 {pending_total}（{json.dumps(pending, ensure_ascii=False)}）、"
        f"claimed 合计 {claimed_total}（{json.dumps(claimed, ensure_ascii=False)}）；"
        f"ceo-seat/tasks {tasks_n} 项；"
        f"缺失目录按0计：{'; '.join(missing_dirs) if missing_dirs else '无'}。\n"
        f"- dispatch-ack：state/dispatch-ack.log mtime={ack_mtime}，年龄={ack_age}s，"
        f"判死(>{DEAD_SECONDS_ACK}s)={'yes' if ack_dead else 'no'}；"
        f"copy mtime={ack_copy_mtime}，年龄={ack_copy_age}s。\n"
        f"- 磁盘：{disk_line('/')}；{disk_line('/data')}。\n"
        f"- 端口(127.0.0.1 TCP)："
        + "; ".join(f"{p}={ports[p]}" for p in PORTS) + "。\n"
        f"- 网关：{docker_gateway_line()}。\n"
        f"- 告警：state/alerts.log {alerts_lines} 行，mtime={alerts_mtime}。\n"
        f"- {model_line}。\n"
        f"- 说明：只读采集，无业务写命令；本轮写盘即治愈 patrol 前值超龄。\n"
    )
    atomic_write(OUT, body)
    with open(os.path.join(STATE_DIR, "runs.log"), "a",
              encoding="utf-8") as f:
        f.write(f"{run_ts} patrol_written patrol_age_before={patrol_age_before} "
                f"dispatch_ack_age={ack_age} pending={pending_total} "
                f"claimed={claimed_total} model={model_line}\n")
    print(f"patrol_written {run_ts} patrol_age_before={patrol_age_before} "
          f"dispatch_ack_age={ack_age} pending={pending_total} "
          f"claimed={claimed_total}")
    print(model_line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
