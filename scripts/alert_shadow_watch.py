#!/usr/bin/env python3
# alert_shadow_watch.py - SHADOW MODE ONLY (2026-10-21, quota-economy plan A-package steps 1+2)
# Purpose: mirror, on the hub and at zero LLM cost, the wake decisions that the
# Claude-side crons currently make by polling:
#   queue   - worker-queue-alert-watch (2m): state/queue_alerts.json active-set diff
#   inbox   - twt2026-ops-watch (10m) + noesis-inbox-watch (20m): new/changed files
#             in /home/ubuntu/shared/twt2026-inbox (Noesis -> CEO mailbox, per
#             shared/PROTOCOL.md; both live watches read this directory)
#   gateway - twt2026-ops-watch gateway check, derived ONLY from landed hub state:
#             RSS pressure   = pm:gateway / PM_RSS_HIGH present in queue_alerts.json
#             container down = latest "cluster-watch: gateway container NOT running"
#                              line in state/alerts.log is not older than the latest
#                              completed cluster_watch run in logs/cluster-watch.log
# This script NEVER sends anything and NEVER touches the live chain. It only
# appends "should-have-woken" events to state/shadow-wake.jsonl so the chains
# can be reconciled (acceptance line: missed wakes = 0) before any switch.
# Inputs (read-only):  state/queue_alerts.json, state/alerts.log,
#                      shared/twt2026-inbox/, logs/cluster-watch.log
# State (own file):    state/.alert_shadow_state.json
# Output (append):     state/shadow-wake.jsonl
# Test hooks: SHADOW_QA_FILE / SHADOW_INBOX_DIR / SHADOW_ALERTS_LOG /
#             SHADOW_CW_LOG / SHADOW_STATE_FILE / SHADOW_OUT_FILE /
#             SHADOW_LOCK_FILE override paths; SHADOW_SELFTEST=1 tags "test": true.
import json, os, re, sys, hashlib, fcntl
from datetime import datetime, timezone

BASE = "/home/ubuntu/ai-team"
QA_F = os.environ.get("SHADOW_QA_FILE", os.path.join(BASE, "state/queue_alerts.json"))
ALERTS_LOG = os.environ.get("SHADOW_ALERTS_LOG", os.path.join(BASE, "state/alerts.log"))
CW_LOG = os.environ.get("SHADOW_CW_LOG", os.path.join(BASE, "logs/cluster-watch.log"))
INBOX_DIR = os.environ.get("SHADOW_INBOX_DIR", "/home/ubuntu/shared/twt2026-inbox")
STATE_F = os.environ.get("SHADOW_STATE_FILE", os.path.join(BASE, "state/.alert_shadow_state.json"))
OUT_F = os.environ.get("SHADOW_OUT_FILE", os.path.join(BASE, "state/shadow-wake.jsonl"))
LOCK_F = os.environ.get("SHADOW_LOCK_FILE", os.path.join(BASE, "state/.alert_shadow.lock"))
SELFTEST = os.environ.get("SHADOW_SELFTEST") == "1"

TS_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")
DOWN_MARK = "gateway container NOT running"


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def fp(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


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


def emit(rec):
    rec["ts"] = now_iso()
    if SELFTEST:
        rec["test"] = True
    with open(OUT_F, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def scan_inbox():
    cur = {}
    try:
        names = sorted(os.listdir(INBOX_DIR))
    except FileNotFoundError:
        return cur
    for name in names:
        p = os.path.join(INBOX_DIR, name)
        try:
            if os.path.isfile(p):
                st = os.stat(p)
                cur[name] = (fp("%s|%d|%d" % (name, st.st_size, int(st.st_mtime))),
                             st.st_size)
        except OSError:
            continue
    return cur


def tail_text(path, nbytes):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - nbytes))
            return f.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def last_cw_done_ts():
    best = None
    for ln in tail_text(CW_LOG, 65536).splitlines():
        if "cluster-watch: done" in ln:
            m = TS_RE.search(ln)
            if m and (best is None or m.group(1) > best):
                best = m.group(1)
    return best


def last_down_ts_from_log():
    best = None
    for ln in tail_text(ALERTS_LOG, 262144).splitlines():
        if DOWN_MARK in ln:
            m = TS_RE.search(ln)
            if m and (best is None or m.group(1) > best):
                best = m.group(1)
    return best


def main():
    lock = open(LOCK_F, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0  # previous run still active; skip quietly

    state = load_json(STATE_F, {})

    # ---------- queue line (step 1, unchanged semantics) ----------
    queue_init = "seen" in state
    seen = state.get("seen", {})  # key "task_id|kind" -> first_seen_iso

    qa = load_json(QA_F, {"alerts": []})
    current = {}
    for a in qa.get("alerts", []):
        key = "%s|%s" % (a.get("task_id", "?"), a.get("kind", "?"))
        current[key] = a
    set_fp = fp("|".join(sorted(current.keys())))

    # alerts.log incremental read (byte offset + inode guard against rotation)
    log_lines = []
    st = None
    new_offset = state.get("log_offset", 0)
    try:
        st = os.stat(ALERTS_LOG)
        offset = state.get("log_offset", 0)
        if state.get("log_inode") != st.st_ino or st.st_size < offset:
            offset = 0
        with open(ALERTS_LOG, "r", errors="replace") as f:
            f.seek(offset)
            log_lines = [ln.rstrip("\n") for ln in f if ln.strip()]
            new_offset = f.tell()
    except FileNotFoundError:
        st, new_offset = None, 0

    if not queue_init:
        # Baseline: adopt the current active set and log EOF; replay nothing.
        emit({"event_type": "baseline", "source": "queue",
              "summary": "shadow start; active=%d" % len(current),
              "active_keys": sorted(current.keys()),
              "fingerprint": set_fp})
        state["seen"] = {k: now_iso() for k in current}
        if st is not None:
            state["log_offset"] = st.st_size
            state["log_inode"] = st.st_ino
        gw_log_lines = []  # gateway baseline scans history itself
    else:
        for key in sorted(set(current) - set(seen)):
            a = current[key]
            emit({"event_type": "wake_new", "source": "queue",
                  "kind": a.get("kind"), "task_id": a.get("task_id"),
                  "summary": a.get("detail", ""),
                  "age_s": a.get("age_s"),
                  "fingerprint": fp(key + "|" + str(a.get("since", ""))),
                  "set_fingerprint": set_fp})
            seen[key] = now_iso()
        for key in sorted(set(seen) - set(current)):
            task_id, kind = key.split("|", 1)
            emit({"event_type": "wake_resolved", "source": "queue",
                  "kind": kind, "task_id": task_id,
                  "summary": "alert cleared from active set",
                  "fingerprint": fp(key + "|resolved"),
                  "set_fingerprint": set_fp})
            del seen[key]
        for ln in log_lines:
            emit({"event_type": "alerts_log", "source": "queue", "summary": ln[:300],
                  "fingerprint": fp(ln)})
        state["seen"] = seen
        if st is not None:
            state["log_offset"] = new_offset
            state["log_inode"] = st.st_ino
        gw_log_lines = log_lines

    # ---------- inbox line (step 2) ----------
    cur_inbox = scan_inbox()
    inbox_seen = state.get("inbox_seen")
    if inbox_seen is None:
        emit({"event_type": "baseline", "source": "inbox",
              "summary": "shadow inbox start; files=%d" % len(cur_inbox),
              "active_keys": sorted(cur_inbox.keys()),
              "fingerprint": fp("|".join(sorted(cur_inbox.keys())))})
    else:
        for name in sorted(set(cur_inbox) - set(inbox_seen)):
            emit({"event_type": "wake_new", "source": "inbox",
                  "kind": "INBOX_NEW", "task_id": name,
                  "summary": "new inbox file %s (%d bytes)" % (name, cur_inbox[name][1]),
                  "fingerprint": fp(name + "|" + cur_inbox[name][0]),
                  "set_fingerprint": fp("|".join(sorted(cur_inbox.keys())))})
        for name in sorted(set(cur_inbox) & set(inbox_seen)):
            if cur_inbox[name][0] != inbox_seen[name]:
                emit({"event_type": "wake_new", "source": "inbox",
                      "kind": "INBOX_UPDATED", "task_id": name,
                      "summary": "inbox file changed %s (%d bytes)" % (name, cur_inbox[name][1]),
                      "fingerprint": fp(name + "|" + cur_inbox[name][0]),
                      "set_fingerprint": fp("|".join(sorted(cur_inbox.keys())))})
        # Files that vanished are dropped silently: the live inbox watches
        # have no "resolved" action for mailbox entries (documented asymmetry).
    state["inbox_seen"] = {n: v[0] for n, v in cur_inbox.items()}

    # ---------- gateway line (step 2) ----------
    rss_now = any(k.startswith("pm:gateway|") for k in current)
    done_ts = last_cw_done_ts()
    gw = state.get("gw")
    if gw is None:
        down_ts = last_down_ts_from_log()
        cont_bad = bool(down_ts) and (done_ts is None or down_ts >= done_ts)
        emit({"event_type": "baseline", "source": "gateway",
              "summary": "shadow gateway start; rss_bad=%s container_bad=%s" % (rss_now, cont_bad),
              "rss_bad": rss_now, "container_bad": cont_bad,
              "fingerprint": fp("gw|%s|%s" % (rss_now, cont_bad))})
        state["gw"] = {"rss": rss_now, "container": cont_bad, "down_ts": down_ts}
    else:
        down_ts = gw.get("down_ts")
        for ln in gw_log_lines:
            if DOWN_MARK in ln:
                m = TS_RE.search(ln)
                if m and (down_ts is None or m.group(1) > down_ts):
                    down_ts = m.group(1)
        cont_now = bool(down_ts) and (done_ts is None or down_ts >= done_ts)
        if rss_now != bool(gw.get("rss")):
            if rss_now:
                emit({"event_type": "wake_new", "source": "gateway",
                      "kind": "PM_RSS_HIGH", "task_id": "pm:gateway",
                      "summary": "gateway RSS pressure alert active in queue_alerts.json",
                      "fingerprint": fp("pm:gateway|PM_RSS_HIGH"),
                      "set_fingerprint": set_fp})
            else:
                emit({"event_type": "wake_resolved", "source": "gateway",
                      "kind": "PM_RSS_HIGH", "task_id": "pm:gateway",
                      "summary": "gateway RSS pressure cleared from queue_alerts.json",
                      "fingerprint": fp("pm:gateway|PM_RSS_HIGH|resolved"),
                      "set_fingerprint": set_fp})
            gw["rss"] = rss_now
        if cont_now != bool(gw.get("container")):
            if cont_now:
                emit({"event_type": "wake_new", "source": "gateway",
                      "kind": "GATEWAY_DOWN", "task_id": "gateway-container",
                      "summary": "cluster-watch reports gateway container NOT running (alerts.log %s)" % down_ts,
                      "fingerprint": fp("gateway-container|down|" + str(down_ts)),
                      "set_fingerprint": set_fp})
            else:
                emit({"event_type": "wake_resolved", "source": "gateway",
                      "kind": "GATEWAY_DOWN", "task_id": "gateway-container",
                      "summary": "cluster-watch completed a run after the down report; no new down line",
                      "fingerprint": fp("gateway-container|resolved|" + str(down_ts)),
                      "set_fingerprint": set_fp})
            gw["container"] = cont_now
        gw["down_ts"] = down_ts
        state["gw"] = gw

    save_json(STATE_F, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
