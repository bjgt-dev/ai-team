#!/usr/bin/env python3
# shadow_reconcile.py - daily reconciliation of the hub shadow watcher against
# the live Claude-side chains (quota-economy plan A-package step 2).
# Read-only inputs:
#   state/shadow-wake.jsonl                              (shadow events)
#   state/alerts.log                                     (landed hub event log)
#   state/reconcile-inputs/queue_alerts_seen.json        (copy of the live
#        worker-queue-alert-watch seen file from the CEO VM; refresh daily)
#   state/reconcile-inputs/mailbox-seen.txt              (copy of the live
#        twt2026-ops-watch seen file from the CEO VM; refresh daily)
# Output: state/shadow-reconcile-YYYYMMDD.txt (also printed to stdout).
# Usage: shadow_reconcile.py [YYYYMMDD] [--include-test]
# A "suspected missed wake" is an event the LIVE chain recorded but the shadow
# never captured. Shadow-only events are listed separately (usually sampling
# latency in the live chain's favour of the shadow, i.e. shadow saw it first).
import json, os, re, sys
from datetime import datetime

BASE = "/home/ubuntu/ai-team"
STATE = os.path.join(BASE, "state")
SHADOW_F = os.path.join(STATE, "shadow-wake.jsonl")
ALERTS_LOG = os.path.join(STATE, "alerts.log")
IN_DIR = os.path.join(STATE, "reconcile-inputs")
QSEEN_F = os.path.join(IN_DIR, "queue_alerts_seen.json")
MSEEN_F = os.path.join(IN_DIR, "mailbox-seen.txt")
DOWN_MARK = "gateway container NOT running"


def load_shadow():
    evs = []
    try:
        with open(SHADOW_F, errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    evs.append(json.loads(ln))
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    return evs


def ev_day(e):
    return (e.get("ts") or "")[:10].replace("-", "")


def split_key(k):
    for sep in ("|", "+", ":"):
        if sep in k:
            a, b = k.split(sep, 1)
            return a.strip(), b.strip()
    return k.strip(), ""


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    include_test = "--include-test" in sys.argv
    day = args[0] if args else datetime.now().strftime("%Y%m%d")
    day_iso = "%s-%s-%s" % (day[:4], day[4:6], day[6:8])
    out = []
    out.append("shadow reconcile %s (generated %s, test events %s)" % (
        day_iso, datetime.now().isoformat(timespec="seconds"),
        "included" if include_test else "excluded"))

    evs = [e for e in load_shadow() if include_test or not e.get("test")]
    news = [e for e in evs if e.get("event_type") == "wake_new"]
    news_day = [e for e in news if ev_day(e) == day]

    # ---------------- queue line ----------------
    out.append("")
    out.append("[queue] worker-queue-alert-watch vs shadow(queue)")
    q_news = [e for e in news_day if e.get("source", "queue") == "queue"]
    q_res = [e for e in evs if e.get("event_type") == "wake_resolved"
             and e.get("source", "queue") == "queue" and ev_day(e) == day]
    out.append("shadow captured (wake_new): %d; wake_resolved: %d" % (len(q_news), len(q_res)))
    shadow_q_keys = {(e.get("task_id"), e.get("kind")) for e in news
                     if e.get("source", "queue") == "queue" and ev_day(e) <= day}
    if os.path.exists(QSEEN_F):
        try:
            seen = json.load(open(QSEEN_F))
        except Exception:
            seen = {}
        live_today = 0
        suspects = []
        for k, v in seen.items():
            tid, kind = split_key(k)
            if isinstance(v, str) and v[:10].replace("-", "") == day:
                live_today += 1
            if (tid, kind) not in shadow_q_keys:
                suspects.append(k)
        out.append("live captured (seen entries first reported today): %d "
                   "(snapshot holds %d outstanding keys; resolved keys vanish, "
                   "so this is a lower bound)" % (live_today, len(seen)))
        out.append("suspected missed wakes: %d" % len(suspects))
        for s in suspects:
            out.append("  SUSPECT queue %s (in live seen, no shadow wake_new)" % s)
    else:
        out.append("live captured: n/a (input not synced: %s missing)" % QSEEN_F)
        out.append("suspected missed wakes: n/a")

    # ---------------- inbox line ----------------
    out.append("")
    out.append("[inbox] twt2026-ops-watch + noesis-inbox-watch vs shadow(inbox)")
    i_news = [e for e in news_day if e.get("source") == "inbox"]
    out.append("shadow captured (wake_new): %d" % len(i_news))
    covered = set()
    for e in evs:
        if e.get("source") == "inbox":
            if e.get("event_type") == "baseline":
                covered.update(e.get("active_keys") or [])
            elif e.get("event_type") == "wake_new":
                covered.add(e.get("task_id"))
    shadow_i_names = {e.get("task_id") for e in news if e.get("source") == "inbox"}
    if os.path.exists(MSEEN_F):
        seen_names = [ln.strip() for ln in open(MSEEN_F, errors="replace") if ln.strip()]
        suspects = [n for n in seen_names if n not in covered]
        shadow_only = sorted(n for n in shadow_i_names if n not in set(seen_names))
        out.append("live captured (mailbox-seen.txt entries, cumulative): %d" % len(seen_names))
        out.append("suspected missed wakes: %d" % len(suspects))
        for s in suspects:
            out.append("  SUSPECT inbox %s (in live seen, never in shadow log; "
                       "if it predates the shadow baseline it is history, not a miss)" % s)
        out.append("shadow-only (captured by shadow, not yet in live seen): %d" % len(shadow_only))
        for s in shadow_only:
            out.append("  SHADOW-ONLY inbox %s" % s)
    else:
        out.append("live captured: n/a (input not synced: %s missing)" % MSEEN_F)
        out.append("suspected missed wakes: n/a")

    # ---------------- gateway line ----------------
    out.append("")
    out.append("[gateway] twt2026-ops-watch gateway check vs shadow(gateway)")
    g_news = [e for e in news_day if e.get("source") == "gateway"]
    out.append("shadow captured (wake_new): %d" % len(g_news))
    starts = [e["ts"] for e in evs if e.get("event_type") == "baseline" and e.get("ts")]
    shadow_start = None
    if starts:
        s0 = min(starts)
        shadow_start = s0[:10] + " " + s0[11:19]

    def line_ts(ln):
        m = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", ln)
        if m:
            return m.group(1)
        m = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})", ln)
        if m:
            return m.group(1) + " " + m.group(2)
        return None

    ref_rss = ref_down = pre_rss = pre_down = 0
    try:
        with open(ALERTS_LOG, errors="replace") as f:
            for ln in f:
                if day_iso not in ln[:32]:
                    continue
                is_rss = "PM_RSS_HIGH" in ln
                is_down = DOWN_MARK in ln
                if not (is_rss or is_down):
                    continue
                ts = line_ts(ln)
                if ts and shadow_start and ts < shadow_start:
                    # Predates the shadow watcher: by design nothing is
                    # replayed, so it cannot count as a missed wake.
                    if is_rss:
                        pre_rss += 1
                    if is_down:
                        pre_down += 1
                    continue
                if is_rss:
                    ref_rss += 1
                if is_down:
                    ref_down += 1
    except FileNotFoundError:
        pass
    out.append("reference events in alerts.log today (shadow period only): "
               "PM_RSS_HIGH lines=%d, gateway-down lines=%d "
               "(pre-shadow lines ignored: PM_RSS_HIGH=%d, gateway-down=%d)"
               % (ref_rss, ref_down, pre_rss, pre_down))
    base = {}
    for e in evs:
        if e.get("source") == "gateway" and e.get("event_type") == "baseline":
            base = e  # last baseline wins
    suspects = []
    if ref_rss:
        has = any(e.get("kind") == "PM_RSS_HIGH" for e in g_news) or \
            (base.get("rss_bad") and ev_day(base) == day)
        if not has:
            suspects.append("PM_RSS_HIGH lines in alerts.log but no shadow gateway wake_new")
    if ref_down:
        has = any(e.get("kind") == "GATEWAY_DOWN" for e in g_news) or \
            (base.get("container_bad") and ev_day(base) == day)
        if not has:
            suspects.append("gateway-down lines in alerts.log but no shadow gateway wake_new")
    out.append("live captured: n/a (ops-watch keeps no landed seen record for "
               "gateway faults; alerts.log lines above are the reference set)")
    out.append("suspected missed wakes: %d" % len(suspects))
    for s in suspects:
        out.append("  SUSPECT gateway %s" % s)

    out.append("")
    out.append("notes: missed-wake=0 per line is the switch criterion, together "
               "with >=48h of shadow running and >=3 real events per line "
               "(see scripts/SHADOW-README.md).")
    text = "\n".join(out) + "\n"
    out_f = os.path.join(STATE, "shadow-reconcile-%s.txt" % day)
    with open(out_f, "w") as f:
        f.write(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
