#!/usr/bin/env python3
"""Butler seat auto-revive (hub cron, every 5 min).
Rules, fixed by task 2026-10-27:
- Dual-standard (expert EXPERT-BUTLER-REVIVE-20261028, implemented 2026-10-28):
  Revival acceptance (after a trigger, all three): (a) a new ACK line in
  dispatch-ack.log after the trigger in plain-text format
  "YYYY-MM-DD HH:MM:SS ACK model=... task=..."; (b) a post-trigger receipt
  in out/ with output_sha256 = 64 hex and created_at within 600s of file
  mtime; (c) patrol refreshed after the trigger and age <= 300s.
  Daily alive (2026-10-28 ACK300 fix): Alive ONLY if PatrolFresh AND
  ack_age <= 300s AND a recent valid receipt exists AND no backlog.
  If ack_age > 300s OR no recent valid receipt, the seat MUST trigger the
  runner regardless of patrol freshness: deterministic patrol alone must
  never count as the seat being alive. Dead = not PatrolFresh OR
  ack_age > 300s OR no recent valid receipt OR backlog.
  Backlog is parsed from patrol-latest.md (pending total + claimed); if it
  cannot be parsed, backlog is conservatively True.
  ACK_THRESHOLD_SECONDS=1800 is an implementation parameter (the expert
  report states "ACK age <= threshold" without a numeric value).
- Deterministic patrol refresh alone NEVER clears a B episode: only a
  triple-satisfied REVIVED clears last_b_episode.
- Brain-blocked (patrol brain failing / gateway 429 in the last 60 min):
  do NOT run the seat runner, record B once and wait for the limit to ease.
- After a run, revived iff: new ACK after trigger AND patrol age <= 300s
  AND a post-trigger receipt is valid. A fake receipt (sha256 missing /
  'uncomputed' / not 64 hex, created_at >600s from file mtime, or a claim
  that patrol refreshed while patrol age >300s) is不合格, never revived.
- B is recorded once per dead episode (episode key = patrol_mtime + ack_mtime).
  A second run with the same patrol mtime writes no new B and triggers nothing.
- 2026-11-01 fix: if the runner exits with stderr containing seat_lock_busy,
  another live seat session holds the lock; treat it as alive-in-progress,
  record no B and send no notification (stops false_dead queue flood).
- This script only reads/writes its own state/audit/B files, plus the one
  authorised seat runner (ceo-opencode.sh). No business commands, no
  credential files, no external publish, no irreversible action.
Exit 0 on every handled outcome (alive / revived / B recorded / B skipped);
non-zero only on an internal error (bad args, unwritable state dir).
"""
import argparse, json, os, re, subprocess, sys, time
from datetime import datetime, timezone

DEAD_SECONDS = 300
ACK_THRESHOLD_SECONDS = 1800
BRAIN_WINDOW = 3600
RECEIPT_TIME_TOLERANCE = 600
MAX_FAILED_TRIGGERS = 3
CAP_COOLDOWN_CYCLES = 3
SHA_RE = re.compile(r"^output_sha256\s*[:=]\s*([^\s]+)", re.MULTILINE)
CREATED_RE = re.compile(r"created_at\s*[:=]\s*([0-9T:\-+:.Z]+)")  # 2026-10-28 fix: accept = and : separators; old regex mis-parsed colon form as ":"

def mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None

def age(path, now):
    mt = mtime(path)
    return None if mt is None else now - mt


def has_backlog(patrol_path):
    """True if patrol reports pending>0 or claimed>0; True if unparseable."""
    try:
        text = open(patrol_path, errors="ignore").read()
    except OSError:
        return True
    mp = re.search(r"pending[^0-9]*?(\d+)", text)
    mc = re.search(r"claimed[^0-9]*?(\d+)", text)
    if mp is None and mc is None:
        return True
    pending = int(mp.group(1)) if mp else 0
    claimed = int(mc.group(1)) if mc else 0
    return (pending > 0) or (claimed > 0)

def ack_line_after_trigger(ack_path, trigger):
    """True if ack log has a plain-text ACK line dated after trigger (CST)."""
    try:
        lines = open(ack_path, errors="ignore").read().splitlines()
    except OSError:
        return False
    for line in lines:
        m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) ACK model=\S+ task=\S", line.strip())
        if not m:
            continue
        try:
            from datetime import datetime as _dt
            ts = _dt.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
            ts += 8 * 3600
        except Exception:
            continue
        if ts >= trigger - 5:
            return True
    return False

def parse_time(s):
    try:
        s = s.strip().strip("`\"'")
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None

def brain_blocked(patrol_rounds, patrol_log, gateway_dir, now):
    """True if the patrol brain is failing in the last 60 min."""
    reasons = []
    try:
        if patrol_rounds and os.path.exists(patrol_rounds):
            with open(patrol_rounds, errors="ignore") as f:
                lines = f.read().strip().splitlines()[-5:]
            for line in reversed(lines):
                if "patrol-round end rc=" in line:
                    m = re.search(r"patrol-round end rc=(\d+)", line)
                    rc = m.group(1) if m else line.rsplit("rc=", 1)[1].strip()
                    # round timestamp is the start line's date; use file mtime as proxy
                    if rc != "0" and now - mtime(patrol_rounds) < BRAIN_WINDOW:
                        if "stale-out-model" not in line:
                            reasons.append("patrol_round_rc=%s" % rc)
                    break
    except OSError:
        pass
    try:
        if patrol_log and os.path.exists(patrol_log) and now - mtime(patrol_log) < BRAIN_WINDOW:
            with open(patrol_log, errors="ignore") as f:
                content = f.read()
            # 2026-10-28 ACK300 fix: only the latest patrol-run segment counts.
            # The old whole-file tail kept stale openai_error lines from earlier
            # runs and falsely blocked every trigger for an hour after any
            # successful patrol run rewrote the log.
            segments = content.split("cron_flock name=patrol")
            tail = segments[-1] if segments else content
            if "429" in tail or "openai_error" in tail:
                reasons.append("patrol_log_brain_error")
    except OSError:
        pass
    try:
        if gateway_dir and os.path.isdir(gateway_dir):
            for name in os.listdir(gateway_dir):
                p = os.path.join(gateway_dir, name)
                if now - (mtime(p) or 0) < BRAIN_WINDOW:
                    with open(p, errors="ignore") as f:
                        if "429" in f.read():
                            reasons.append("gateway_retest_429:" + name)
                            break
    except OSError:
        pass
    return reasons

def check_receipt(out_dir, trigger_start, patrol_age):
    """Return (valid, fake_reasons, candidate_path)."""
    candidates = []
    try:
        for name in os.listdir(out_dir):
            p = os.path.join(out_dir, name)
            if os.path.isfile(p) and (mtime(p) or 0) >= trigger_start - 5:
                candidates.append(p)
    except OSError:
        return False, ["out_dir_unreadable"], None
    if not candidates:
        return False, ["no_post_trigger_receipt"], None
    # 2026-10-28 resident fix: scan ALL post-trigger candidates, do not fail
    # on the first non-receipt file (patrol-latest.md / dispatch-ack-copy.log
    # are not receipts). A valid receipt anywhere in the set counts.
    ordered = sorted(candidates, key=lambda x: mtime(x) or 0, reverse=True)
    receipt_like = [x for x in ordered if "receipt" in x.lower() or "revival" in x.lower()]
    scan = receipt_like + [x for x in ordered if x not in receipt_like]
    first_fake = None
    for p in scan:
        name = p.rsplit("/", 1)[-1]
        if name in ("patrol-latest.md", "dispatch-ack-copy.log") or ".bak-" in name:
            continue
        try:
            text = open(p, errors="ignore").read()
        except OSError:
            continue
        fake = []
        m = SHA_RE.search(text)
        if not m or not re.fullmatch(r"[0-9a-fA-F]{64}", m.group(1)):
            fake.append("sha256_missing_or_not_64hex")
        cm = CREATED_RE.search(text)
        if cm:
            ct = parse_time(cm.group(1))
            if ct is None or abs(ct - (mtime(p) or 0)) > RECEIPT_TIME_TOLERANCE:
                fake.append("created_at_mismatch_vs_mtime")
        if ("已刷新" in text or "已恢复" in text) and "patrol" in text and patrol_age is not None and patrol_age > DEAD_SECONDS:
            fake.append("claims_patrol_refreshed_but_patrol_stale")
        if not fake:
            return True, [], p
        if first_fake is None:
            first_fake = (fake, p)
    if first_fake:
        return False, first_fake[0], first_fake[1]
    return False, ["no_readable_receipt"], None

def load_state(state_dir):
    p = os.path.join(state_dir, "state.json")
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state_dir, state):
    with open(os.path.join(state_dir, "state.json"), "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)

def audit(state_dir, record):
    with open(os.path.join(state_dir, "audit.jsonl"), "a") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

def record_b(state_dir, episode, reason, evidence):
    bpath = os.path.join(state_dir, "B-%s.json" % episode)
    if os.path.exists(bpath):
        return False
    rec = {"verdict": "B", "episode": episode, "reason": reason,
           "evidence": evidence, "at": datetime.now(timezone.utc).isoformat()}
    with open(bpath, "w") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2, sort_keys=True)
    # 2026-11-02 令03一2：一2同一状态只写一条决策（状态哈希去重：以 ack_mtime + reason 为状态键）。
    # 若已有同状态待决条目，只写 B 审计，不再推新决策。队列不可读时 fail open（照旧推送）。
    # 2026-11-02 令03一3：PAUSE_DECISION 开关存在时，暂停本循环的写决策动作（其余照旧），可逆（删除即恢复）。
    _pause = os.path.join(state_dir, "PAUSE_DECISION")
    _skip_push = False
    if os.path.exists(_pause):
        _skip_push = True
    else:
        try:
            _ack = str(episode).rsplit("-", 1)[-1]
            _q = json.load(open("/home/ubuntu/ai-team/state/decision-queue.json"))
            for _x in _q.get("pending", []):
                _tid = str(_x.get("task_id", ""))
                if _tid.startswith("butler-seat-") and _tid.rsplit("-", 1)[-1] == _ack and str(reason) in str(_x.get("summary", "")):
                    _skip_push = True
                    break
        except Exception:
            _skip_push = False
    if _skip_push:
        try:
            audit(state_dir, {"at": datetime.now(timezone.utc).isoformat(), "verdict": "B", "episode": episode, "reason": reason, "push": "skipped_same_state_or_paused", "evidence": evidence})
        except Exception:
            pass
        return True
    # L8：死信首次落盘即直通notify.py推给Twt，不许只写日志（notify按task_id去重，不重复推同一死信）
    try:
        subprocess.run(["python3", "/home/ubuntu/ai-team/ceo-seat/work/notify.py", "push", "--type", "NEEDS_DECISION", "--task-id", "butler-seat-" + str(episode), "--summary", "大管家席位死信：" + str(reason), "--options", "A:人工接手处理|B:暂缓，先观察"], capture_output=True, text=True, timeout=60)
    except Exception:
        pass
    return True


def has_recent_valid_receipt(out_dir, now, window=None):
    window = DEAD_SECONDS if window is None else window  # 2026-10-28 ACK300 v2: receipt window = dead window
    try:
        names = os.listdir(out_dir)
    except OSError:
        return False
    for name in names:
        if name in ("patrol-latest.md", "dispatch-ack-copy.log") or ".bak-" in name:
            continue
        path = os.path.join(out_dir, name)
        if not os.path.isfile(path):
            continue
        mt = mtime(path)
        if mt is None or now - mt > window:
            continue
        try:
            text = open(path, errors="ignore").read()
        except OSError:
            continue
        m = SHA_RE.search(text)
        if not m or not re.fullmatch(r"[0-9a-fA-F]{64}", m.group(1)):
            continue
        cm = CREATED_RE.search(text)
        if cm:
            ct = parse_time(cm.group(1))
            if ct is None or abs(ct - mt) > RECEIPT_TIME_TOLERANCE:
                continue
        return True
    return False

def main():
    if os.path.exists("/home/ubuntu/ai-team/state/priority-external-task"):
        print("seat_revive skipped: priority-external-task present")
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--patrol", default="/home/ubuntu/ai-team/ceo-seat/out/patrol-latest.md")
    ap.add_argument("--ack", default="/home/ubuntu/ai-team/state/dispatch-ack.log")
    ap.add_argument("--out-dir", default="/home/ubuntu/ai-team/ceo-seat/out")
    ap.add_argument("--runner", default="/home/ubuntu/ai-team/scripts/ceo-opencode.sh")
    ap.add_argument("--state-dir", default="/home/ubuntu/ai-team/state/butler-seat-revive")
    ap.add_argument("--patrol-rounds", default="/home/ubuntu/ai-team/state/patrol-rounds.log")
    ap.add_argument("--patrol-log", default="/home/ubuntu/ai-team/logs/patrol.log")
    ap.add_argument("--gateway-dir", default="/home/ubuntu/ai-team/state/gateway-retest-20261027")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--task", default="救活复核：恢复巡航，刷新 ceo-seat/out/patrol-latest.md，回执须含工单号、状态、回执四字段（output_sha256 须为真实 64 位十六进制、created_at 与文件时间一致）、证据路径、验收结论；巡航未刷新不得声称已刷新。")
    args = ap.parse_args()
    os.makedirs(args.state_dir, exist_ok=True)
    now = time.time()
    p_age = age(args.patrol, now)
    a_age = age(args.ack, now)
    p_mt = mtime(args.patrol)
    state = load_state(args.state_dir)
    base = {"at": datetime.now(timezone.utc).isoformat(), "patrol_age_s": p_age,
            "ack_age_s": a_age, "patrol_mtime": p_mt}
    if p_age is None:
        base.update(action="B", result="patrol_file_missing")
        audit(args.state_dir, base)
        record_b(args.state_dir, "no-patrol-file", "patrol_file_missing", base)
        print(json.dumps(base, ensure_ascii=False))
        return 0
    a_mt = mtime(args.ack)
    backlog = has_backlog(args.patrol)
    base.update(ack_mtime=a_mt, backlog=backlog, ack_threshold_s=ACK_THRESHOLD_SECONDS)
    patrol_fresh = p_age <= DEAD_SECONDS
    recent_receipt = has_recent_valid_receipt(args.out_dir, now)
    base.update(recent_valid_receipt=recent_receipt)
    daily_alive = patrol_fresh and (a_age is not None and a_age <= DEAD_SECONDS) and recent_receipt and (not backlog)  # ACK300 v2
    if (not args.force) and daily_alive:
        if state.get('failed_triggers') or state.get('cap_cooldown_left'):
            state['failed_triggers'] = 0
            state['cap_cooldown_left'] = 0
            save_state(args.state_dir, state)
        # Daily alive classification only. Do NOT clear last_b_episode here:
        # a deterministic patrol refresh must not clear a B episode; only a
        # triple-satisfied REVIVED (below) clears it.
        base.update(action="none", result="alive")
        audit(args.state_dir, base)
        print(json.dumps(base, ensure_ascii=False))
        return 0
    # Dead episode (dual-standard): not patrol-fresh, or backlog with stale ACK.
    episode = "%s-%s" % (int(p_mt) if p_mt else "unknown", int(a_mt) if a_mt else 0)
    if (not args.force) and state.get("last_b_episode") == episode:
        base.update(action="none", result="B_already_recorded_for_episode")
        audit(args.state_dir, base)
        print(json.dumps(base, ensure_ascii=False))
        return 0
    if (not args.force) and state.get('cap_cooldown_left', 0) > 0:
        state['cap_cooldown_left'] = int(state['cap_cooldown_left']) - 1
        if state['cap_cooldown_left'] == 0:
            state['failed_triggers'] = 0
        save_state(args.state_dir, state)
        base.update(action='none', result='B_cap_cooldown', cap_cooldown_left=state['cap_cooldown_left'], failed_triggers=state.get('failed_triggers', 0))
        audit(args.state_dir, base)
        print(json.dumps(base, ensure_ascii=False))
        return 0
    brain_all = brain_blocked(args.patrol_rounds, args.patrol_log, args.gateway_dir, now)
    brain = [] if args.force else [r for r in brain_all if r != "patrol_log_brain_error"]
    # 2026-11-01 OPT: 巡航脑失败不等于席位死。席位近30分钟内已用网关出活，
    # 说明席位活着、只是Zen不在，此时推B只会让队列暴涨，不产生新信息。
    # 这种情况只审计、不推B；席位真正30分钟无出活时再推B。
    if brain:
        try:
            _last = None
            _f = "/home/ubuntu/ai-team/state/brain-active.log"
            with open(_f, errors="ignore") as _fh:
                for _line in _fh:
                    if "patrol brain=" not in _line:
                        _last = _line
            if _last and "my-gateway/" in _last:
                _ts = _last.split()[0] + " " + _last.split()[1] if len(_last.split()) >= 2 else ""
                import datetime as _dt
                _dtv = _dt.datetime.strptime(_ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_dt.timezone.utc).timestamp() if _ts else 0
                # brain-active.log 时间是北京时间(+08)，now 是 epoch UTC，两者差8小时需换算：直接用文件mtime近似
                import os as _os
                _age = now - _os.path.getmtime(_f) if _os.path.exists(_f) else 99999
                if _age < 1800:
                    base.update(action="none", result="alive_gateway_brain_blocked_skipped", brain_reasons=brain)
                    audit(args.state_dir, base)
                    print(json.dumps(base, ensure_ascii=False))
                    return 0
        except Exception:
            pass
    if brain:
        base.update(action="B_no_runner", result="brain_blocked", brain_reasons=brain)
        audit(args.state_dir, base)
        record_b(args.state_dir, episode, "brain_blocked", base)
        state["last_b_episode"] = episode
        save_state(args.state_dir, state)
        print(json.dumps(base, ensure_ascii=False))
        return 0
    # 2026-11-02 令03一2：同一席位 30 分钟内最多触发 runner 1 次，带退避；超限只写一条汇总告警，不跑 runner、不写决策、不计失败。
    _now_trig = time.time()
    _last_trig = state.get("last_runner_trigger_epoch", 0)
    if _now_trig - _last_trig < 1800:
        _last_alert = state.get("last_summary_alert_epoch", 0)
        if _now_trig - _last_alert >= 1800:
            try:
                with open(os.path.join(args.state_dir, "summary-alerts.log"), "a") as _sf:
                    _sf.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "seat": "butler", "rule": "30min_1_attempt", "episode": episode, "note": "runner trigger skipped: within 30min window; no new decision written"}, ensure_ascii=False) + "\n")
            except Exception:
                pass
            state["last_summary_alert_epoch"] = _now_trig
            save_state(args.state_dir, state)
        base.update(action="none", result="skipped_30min_gate", episode=episode)
        audit(args.state_dir, base)
        print(json.dumps(base, ensure_ascii=False))
        return 0
    state["last_runner_trigger_epoch"] = _now_trig
    save_state(args.state_dir, state)
    trigger = time.time()
    runner_stderr = ""
    try:
        proc = subprocess.run([args.runner, args.task], timeout=660,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        runner_rc = proc.returncode
        runner_stderr = proc.stderr or ""
    except Exception as e:
        runner_rc = None
        base.update(runner_error=str(e)[:200])
    if "seat_lock_busy" in runner_stderr:
        # 2026-11-01 ARCHFIX: flock 挡下说明另一席位会话正在跑，属"在跑"不是"死"。
        # 旧逻辑会把并发会话的 ACK 当成自己的 ACK，再因无新回执记 fake_receipt，
        # 每轮新 episode 新推一条，队列被席位死信淹没。
        base.update(action="none", result="alive_in_progress_lock_busy", runner_rc=runner_rc)
        audit(args.state_dir, base)
        print(json.dumps(base, ensure_ascii=False))
        return 0
    if runner_rc is None:
        # 2026-11-01 ARCHFIX2: runner 超时（timeout=660）→ OpenCode 会话卡死。
        # 旧逻辑漏过此分支，误判 fake_receipt 并推 B 进决策队列。
        # 超时 = 座位还在跑，不推 B、不触发下轮，只审计退出，等下轮自然重试。
        base.update(action="none", result="runner_timeout_no_b", runner_rc=runner_rc,
                   runner_error=str(base.get("runner_error", ""))[:200])
        audit(args.state_dir, base)
        print(json.dumps(base, ensure_ascii=False))
        return 0
    now2 = time.time()
    p_age2 = age(args.patrol, now2)
    a_mt2 = mtime(args.ack)
    ack_new = ack_line_after_trigger(args.ack, trigger)
    patrol_after_trigger = bool(mtime(args.patrol) and mtime(args.patrol) >= trigger - 5)
    valid, fake, cand = check_receipt(args.out_dir, trigger, p_age2)
    base.update(action="ran_runner", runner_rc=runner_rc, patrol_age_after_s=p_age2,
                ack_new=ack_new, patrol_after_trigger=patrol_after_trigger, receipt_valid=valid, receipt_fake_reasons=fake,
                receipt_candidate=cand)
    if ack_new and patrol_after_trigger and p_age2 is not None and p_age2 <= DEAD_SECONDS and valid:
        base.update(result="REVIVED")
        audit(args.state_dir, base)
        state["last_b_episode"] = None
        state["failed_triggers"] = 0
        state["cap_cooldown_left"] = 0
        save_state(args.state_dir, state)
    else:
        reason = "fake_receipt" if fake else "patrol_not_refreshed"
        if not ack_new:
            reason = "ack_not_new"
        failed = int(state.get('failed_triggers', 0)) + 1
        state['failed_triggers'] = failed
        if failed >= MAX_FAILED_TRIGGERS:
            state['cap_cooldown_left'] = CAP_COOLDOWN_CYCLES
            base.update(failed_triggers=failed, cap_triggered=True, cap_cooldown_left=CAP_COOLDOWN_CYCLES)
        else:
            base.update(failed_triggers=failed)
        base.update(result="B", b_reason=reason)
        audit(args.state_dir, base)
        record_b(args.state_dir, episode, reason, base)
        state["last_b_episode"] = episode
        save_state(args.state_dir, state)
    print(json.dumps(base, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    sys.exit(main())
