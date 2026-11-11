#!/usr/bin/env python3
"""M5 24h window acceptance check (CHAIR-20261105-18 section 3).

Reads hub state only (read-only except writing its own output dir).
No network, no new middleware, no cost, no external calls.
Cross-supplier review is enforced by the review receipts themselves
(producer/reviewer suppliers recorded in the receipt); this script only
counts receipts whose verdict is PASS.

Usage (hub):
  python3 /home/ubuntu/ai-team/scripts/m5-window-check-20261105.py \
      [--start 2026-11-04T20:30] [--end 2026-11-05T20:30]
  Defaults: end=now (CST), start=end-24h.
  Writes state/m5-20261105/window-<END YYYYMMDD-HHMM>.md + .json
  Exit code = number of failed items (0 = 6/6).

Cron (daily 20:35 CST, trailing 24h window ending 20:30):
  35 20 * * * /home/ubuntu/ai-team/scripts/cron_flock.sh m5_window \\
      python3 /home/ubuntu/ai-team/scripts/m5-window-check-20261105.py \\
      >> /home/ubuntu/ai-team/logs/m5-window-check.log 2>&1
"""
import argparse, glob, importlib.util, json, os, re, sys
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))

# M5 field stores (2026-11-06 rework): cost / default-action / degrade-event.
# Imported by absolute path (regular import is blind to it on this hub).
try:
    _spec = importlib.util.spec_from_file_location(
        "m5fields_20261105",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "m5-fields-20261105.py"))
    _m5f = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_m5f)
except Exception:
    _m5f = None

BASE = os.environ.get("AI_TEAM", "/home/ubuntu/ai-team")
OUT_DIR = os.path.join(BASE, "state", "m5-20261105")


def parse_ts(s):
    """Parse 'YYYY-MM-DD HH:MM[:SS]' or ISO with offset; naive = CST."""
    if not s:
        return None
    s = str(s).strip()
    s = re.sub(r"\.\d+", "", s, count=1)  # drop fractional seconds
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            t = datetime.strptime(s, fmt)
            if t.tzinfo is None:
                t = t.replace(tzinfo=CST)
            return t.astimezone(CST)
        except ValueError:
            continue
    return None


def mtime(path):
    try:
        return datetime.fromtimestamp(os.path.getmtime(path), tz=CST)
    except OSError:
        return None


def in_window(t, start, end):
    return t is not None and start <= t <= end


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def fmt_age(seconds):
    if seconds is None:
        return "未知"
    h = seconds / 3600.0
    if h >= 1:
        return "%.1f小时" % h
    return "%.0f分钟" % (seconds / 60.0)


def item(verdict, num, den, evidence, missing=None, note=""):
    return {"verdict": verdict, "success": num, "total": den,
            "evidence": evidence, "missing": missing or [], "note": note}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args()
    end = parse_ts(args.end) if args.end else datetime.now(CST)
    start = parse_ts(args.start) if args.start else end - timedelta(hours=24)
    label = end.strftime("%Y%m%d-%H%M")
    items = {}

    # ---- 1. non-L3 human intervention: 0 ----
    # Proxy A: executor receipts in window whose stdout tail says "manual".
    # Proxy B: any M3/chair-ordered evidence file modified in window
    # (chair-ordered batch work is by definition not L3-autonomous).
    # There is NO authoritative intervention ledger on the hub; if neither
    # proxy fires the item is 缺数据, never assumed 0.
    manual = []
    seen_manual = set()
    for pat in ("state/m3-exit-20261105/2-review-receipt.json",
                "state/l3-20261105-contracts/*/review-record.json",
                "state/**/2-review-receipt.json"):
        for p in glob.glob(os.path.join(BASE, pat), recursive=True):
            mt = mtime(p)
            if not in_window(mt, start, end):
                continue
            try:
                txt = open(p, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            if "manual" in txt.lower() and p not in seen_manual:
                seen_manual.add(p)
                manual.append(os.path.relpath(p, BASE))
    chair_batch = []
    for pat in ("state/m3-exit-20261105/*.md", "state/chair17-20261105/cards/*.md"):
        for p in glob.glob(os.path.join(BASE, pat)):
            if in_window(mtime(p), start, end):
                chair_batch.append(os.path.relpath(p, BASE))
    # Intervention ledger (2026-11-06 instrument fix): without a ledger,
    # item 1 could never pass (only 不通过 or 缺数据). Ledger entries are
    # kind=intervention (manual execution/repair/override: counts) or
    # kind=input/setup (gate-dispatched load, harness setup: logged, not counted).
    ledger_hits = []
    ledger_path = os.path.join(BASE, "state", "m5-20261105", "intervention-ledger.json")
    ledger_exists = os.path.exists(ledger_path)
    if ledger_exists:
        try:
            for e in load_json(ledger_path) or []:
                if isinstance(e, dict) and e.get("kind") == "intervention":
                    if in_window(parse_ts(e.get("ts")), start, end):
                        ledger_hits.append(e)
        except Exception:
            pass
    interventions = len(manual) + (1 if chair_batch else 0) + len(ledger_hits)
    ev1 = ([f"manual执行回执: {x}" for x in manual] +
           ([f"董事长令下批次证据 {len(chair_batch)} 件, 如 {chair_batch[0]}"]
            if chair_batch else []) +
           ([f"台账介入: {e.get('ts')} {e.get('actor')} {e.get('detail', '')[:60]}"
             for e in ledger_hits] if ledger_hits else []) +
           ([f"台账: {ledger_path} 在窗, 0次介入"] if ledger_exists and not ledger_hits else []))
    if interventions > 0:
        items["1"] = item("不通过", 0, 0, ev1, [],
                          "目标0次, 实查>=%d次人工介入记录 (manual执行回执%d + 董事长令批次%d + 台账%d); "
                          "L3队列同期无新自主认领" % (interventions, len(manual),
                                                    1 if chair_batch else 0, len(ledger_hits)))
    elif ledger_exists:
        items["1"] = item("通过", 0, 0, ev1, [],
                          "台账在窗且0次介入, 两代理亦未命中")
    else:
        items["1"] = item("缺数据", 0, 0, [], ["枢纽无人工介入台账, 无法证实0次"],
                          "两代理均未命中, 但无台账故不得判通过")

    # ---- 2. verified deliveries >= 1/day ----
    # Count review PASS records modified in window, production mode only.
    # Excludes TEST runs and production_done_claimed=false.
    deliveries = []
    cands = []
    for pat in ("state/m3-exit-20261105/2-review-receipt.json",
                "state/l3-20261105-contracts/*/review-record.json",
                "state/goal-driver-test-*/g2-item4-acceptance-verdict.json",
                "state/**/g2-item4-acceptance-verdict.json",
                # Independent-review hook output (2026-11-06 fix G): the hook
                # writes state/m5-20261105/review/review-<uuid>.json; the
                # check's paths predate it, so reviewed deliveries never
                # counted. Same verdict/mode filters apply below.
                "state/m5-20261105/review/review-*.json"):
        cands += glob.glob(os.path.join(BASE, pat), recursive=True)
    for p in sorted(set(cands)):
        mt = mtime(p)
        if not in_window(mt, start, end):
            continue
        d = load_json(p)
        if not isinstance(d, dict):
            continue
        verdict = d.get("review_verdict") or d.get("verdict")
        if verdict != "PASS":
            continue
        if d.get("mode") == "TEST" or d.get("production_done_claimed") is False:
            continue
        deliveries.append(os.path.relpath(p, BASE))
    items["2"] = item("通过" if len(deliveries) >= 1 else "不通过",
                      len(deliveries), 1,
                      [f"经验收交付: {x}" for x in deliveries],
                      [] if deliveries else ["窗口内无 review PASS 记录"],
                      "生产模式/独立复核PASS才计数, TEST与production_done_claimed=false已排除")

    # ---- 3. fake DONE 0, reconciliation flat ----
    rej = load_json(os.path.join(BASE, "state", "reject-ledger.json")) or {}
    # Window-scoped trips (2026-11-06 instrument fix): a trip detected in an
    # earlier window must not fail every later window forever. Entries carry
    # detected_at (backfilled 2026-11-05 for the 4 known trips); entries
    # without detected_at still count (fail-closed).
    tripped = 0
    tripped_ids = []
    for _k, _v in rej.items():
        if not (isinstance(_v, dict) and _v.get("tripped")):
            continue
        _dt = parse_ts(_v.get("detected_at")) if _v.get("detected_at") else None
        if _dt is None or in_window(_dt, start, end):
            tripped += 1
            tripped_ids.append(_k)
    dangling = 0
    dangling_ids = []
    acl = os.path.join(BASE, "state", "auto-close-log.json")
    try:
        for line in open(acl, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if in_window(parse_ts(d.get("at")), start, end) and \
                    d.get("reason") == "dangling no artifacts":
                dangling += 1
                dangling_ids.append(str(d.get("id")))
    except OSError:
        pass
    # Latest health_3q supplement inside the window (C2 timer result).
    lease = (None, None)
    sha_ok = None
    best = None
    for p in glob.glob(os.path.join(BASE, "state", "c2-task-results", "*.txt")):
        mt = mtime(p)
        if not in_window(mt, start, end):
            continue
        try:
            txt = open(p, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        if "TIMER=health_3q" not in txt:
            continue
        if best is None or mt > best[0]:
            best = (mt, txt)
    if best:
        m = re.search(r'"lease_reclaim":\s*\{[^}]*"total":\s*(\d+)[^}]*"reclaimed":\s*(\d+)',
                      best[1])
        if m:
            lease = (int(m.group(2)), int(m.group(1)))
        m2 = re.search(r'"done_sha_spotcheck"[:,]\s*(true|false)', best[1])
        sha_ok = (m2.group(1) == "true") if m2 else None
    # Lease flatness (2026-11-06 instrument fix): exact 100% fails whenever
    # a single lease is legitimately ACTIVE, and item 2 requires an in-window
    # delivery (which holds a lease) - the two items conflict by construction.
    # Align with health_3q_supplement's own published threshold (>=99%).
    lease_ok = (lease[0] is not None and lease[1]
                and lease[0] / lease[1] >= 0.99)
    flat = (tripped == 0 and dangling == 0 and lease_ok and sha_ok is True)
    ev3 = [f"reject-ledger tripped(窗内)={tripped}/{len(rej)} ({', '.join(tripped_ids) or '无'})",
           f"窗口内dangling自动关闭={dangling} ({', '.join(dangling_ids) or '无'})",
           f"租约回收={lease[0]}/{lease[1]}" if lease[0] is not None else "租约回收: 窗口内无health_3q记录",
           f"done_sha_spotcheck={sha_ok}"]
    items["3"] = item("通过" if flat else "不通过",
                      (1 if tripped == 0 else 0) + (1 if dangling == 0 else 0) +
                      (1 if lease_ok else 0) +
                      (1 if sha_ok else 0), 4, ev3,
                      [] if flat else ["对账差额见证据: dangling关闭与未回收租约"],
                      "假DONE以reject-ledger tripped计; 对账须四项全平才通过")

    # ---- 4. ownerless 0, oldest pending < 30min, default-action 100% ----
    pending = []  # (id, age_seconds or None, owner?)
    dq = load_json(os.path.join(BASE, "state", "decision-queue.json")) or {}
    for it in dq.get("pending", []) if isinstance(dq, dict) else []:
        age = None
        t = parse_ts(it.get("created_at"))
        if t is not None:
            age = (end - t).total_seconds()
        owner = it.get("owner") or it.get("claimed_by") or it.get("assignee")
        pending.append((str(it.get("id")), age, bool(owner)))
    l3q = load_json(os.path.join(BASE, "state", "l3-v2", "queue.json")) or {}
    for t in l3q.get("tasks", []) if isinstance(l3q, dict) else []:
        if t.get("status") == "PENDING":
            age = None
            ts = parse_ts(t.get("created_at"))
            if ts is not None:
                age = (end - ts).total_seconds()
            owner = bool(t.get("claim_token") or t.get("claimed_by"))
            pending.append((str(t.get("task_id")), age, owner))
    ownerless = [p for p in pending if not p[2]]
    ages = [p[1] for p in pending if p[1] is not None]
    oldest = max(ages) if ages else None
    # Default-action rate: decision-queue items carrying a default_action
    # field; executed = closed by that default action in window. Hub has
    # no such field today, so the rate is undefined, never 100%.
    n_default = 0
    for it in dq.get("pending", []) + dq.get("resolved", []) \
            if isinstance(dq, dict) else []:
        if isinstance(it, dict) and "default_action" in it:
            n_default += 1
    rate_ok = False
    # M5 field store (2026-11-06): default-action records in this window.
    store_rate = None
    store_triggered = 0
    if _m5f is not None:
        try:
            _recs = _m5f.records_in_window(
                _m5f.load_store("default-action"), start, end, "last_updated_at")
            _sum = _m5f.default_action_rate(_recs)
            store_triggered = _sum["total_triggered"]
            store_rate = _sum["rate"]
            rate_ok = (store_rate == 1.0 and store_triggered > 0)
        except Exception:
            store_rate = None
    ev4 = [f"pending合计={len(pending)} (决策队列{len(dq.get('pending', [])) if isinstance(dq, dict) else 0} + L3队列PENDING{len(pending) - (len(dq.get('pending', [])) if isinstance(dq, dict) else 0)})",
           f"无主项={len(ownerless)} ({', '.join(p[0] for p in ownerless) or '无'})",
           f"最老pending={fmt_age(oldest)} (限<30分钟)",
           f"默认动作字段条目={n_default}, 执行率=无法计算 (无默认动作字段/执行记录)" if store_rate is None else
           f"默认动作库: 窗口内触发{store_triggered}条, 已执行{int(store_rate * store_triggered) if store_rate is not None else 0}条, 执行率={store_rate} (来源 state/m5-20261105/default-action.json)"]
    if best:
        m3 = re.search(r'"queue_oldest_ready_h":\s*([0-9.]+)', best[1])
        if m3:
            ev4.append(f"中央队列最老READY={m3.group(1)}小时 (health_3q supplement)")
    # Empty-pending fix (2026-11-06 instrument fix): zero pending items
    # means no item can violate the 30-minute bound; the healthy empty
    # state passes the age sub-item vacuously.
    oldest_ok = (oldest is None) or (oldest < 1800)
    if not pending:
        ev4.append("pending为空, 最老pending不适用(空队列计通过)")
    items["4"] = item("通过" if (len(ownerless) == 0 and oldest_ok
                                 and rate_ok) else "不通过",
                      (1 if len(ownerless) == 0 else 0) +
                      (1 if oldest_ok else 0) +
                      (1 if rate_ok else 0), 3, ev4,
                      [] if rate_ok else ["默认动作无字段/无执行记录, 执行率无法算100%"],
                      "三子项全满足才通过")

    # ---- 5. Tier-1 >= 2 all window; degrade -> no task loss + recovery record ----
    pool_now = load_json(os.path.join(BASE, "state", "gateway-pool.json")) or {}
    tier_counts = []
    if isinstance(pool_now.get("tier_1"), list):
        tier_counts.append(len(pool_now["tier_1"]))
    for p in glob.glob(os.path.join(BASE, "state", "gateway-pool.json.bak-*")):
        if in_window(mtime(p), start, end):
            d = load_json(p) or {}
            if isinstance(d.get("tier_1"), list):
                tier_counts.append(len(d["tier_1"]))
    min_tier1 = min(tier_counts) if tier_counts else None
    # Canary pass3 per model inside the window.
    canary = {}
    canary_pass = {}
    cj = os.path.join(BASE, "state", "gateway-canary-20261104.jsonl")
    try:
        for line in open(cj, encoding="utf-8", errors="ignore"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not in_window(parse_ts(d.get("ts")), start, end):
                continue
            m = d.get("model", "?")
            canary[m] = canary.get(m, 0) + 1
            if d.get("pass3"):
                canary_pass[m] = canary_pass.get(m, 0) + 1
    except OSError:
        pass
    tier1_models = pool_now.get("tier_1", []) if isinstance(pool_now, dict) else []
    canary_str = ", ".join(
        f"{m} {canary_pass.get(m, 0)}/{canary.get(m, 0)}"
        for m in tier1_models) or "无"
    # Degradation half: completed self-repair records in window?
    # M5 field store (2026-11-06): degrade-events with recovered_at in
    # window and verify_result == '通过' count as self-healed repairs.
    repairs_done = 0
    if _m5f is not None:
        try:
            repairs_done += _m5f.self_healed_count(
                _m5f.load_store("degrade-event"),
                start.strftime("%Y-%m-%d %H:%M:%S"),
                end.strftime("%Y-%m-%d %H:%M:%S"))
        except Exception:
            pass
    sr = load_json(os.path.join(BASE, "state", "self-repair-log.json"))
    if isinstance(sr, dict):
        for r in sr.get("repairs", []) if isinstance(sr.get("repairs"), list) else []:
            if isinstance(r, dict) and r.get("status") in ("done", "completed", "fixed"):
                repairs_done += 1
    ev5 = [f"Tier-1快照数={tier_counts} (最小{min_tier1}, 限>=2)",
           f"窗口内金丝雀pass3: {canary_str}",
           "canary_b晋升: " + str((load_json(os.path.join(BASE, 'state', 'canary-b-tally-20261104.json')) or {}).get("promoted", "未知")),
           f"窗口内已完成自愈修复记录={repairs_done} (status=done/completed/fixed)"]
    degrade_ok = (min_tier1 is not None and min_tier1 >= 2 and repairs_done >= 0
                  and lease[0] == lease[1] and lease[0] is not None)
    # Second half needs an explicit degrade event with 0-loss + recovery
    # record; a bare lease match is not that record, so require a repair
    # record OR an explicit no-degrade statement file. Neither exists as
    # a counted artefact today -> second half unproven.
    second_half_proven = repairs_done > 0
    items["5"] = item("通过" if (min_tier1 is not None and min_tier1 >= 2
                                 and second_half_proven) else "不通过",
                      (1 if min_tier1 is not None and min_tier1 >= 2 else 0) +
                      (1 if second_half_proven else 0), 2, ev5,
                      [] if second_half_proven else
                      ["降级事件0丢任务+恢复记录: 窗口内无已完成自愈修复记录, 未证实"],
                      "单轮金丝雀失败只计数不退池 (Q1规则), 故资格以池快照计; 后半须有恢复记录才通过")

    # ---- 6. cost 0, within every Oracle quota ----
    # No billing/cost artefact exists on the hub today; search for one
    # modified in the window. Absence = 缺数据, never assumed 0.
    cost_files = []
    for pat in ("state/*cost*.json", "state/*billing*.json",
                "state/*ocpu*.json", "state/oracle-*usage*.json"):
        for p in glob.glob(os.path.join(BASE, pat)):
            if in_window(mtime(p), start, end):
                cost_files.append(os.path.relpath(p, BASE))
    # M5 field store (2026-11-06): cost records in this window.
    cost_ev = []
    cost_known = False
    if _m5f is not None:
        try:
            _crecs = _m5f.records_in_window(
                _m5f.load_store("cost"), start, end, "recorded_at")
            _csum = _m5f.summarize_cost(_crecs)
            cost_ev = [f"成本库: 窗口内记录{_csum['count']}条 (免费zero_free {_csum['zero_free_count']}条, 已知 {_csum['known_count']}条, 未知 {_csum['unknown_count']}条)",
                       f"OCPU小时累计: {'未知 (无资源计量)' if _csum['total_cpu_hours'] == 0 and _csum['count'] > 0 else _csum['total_cpu_hours']} (限3000)",
                       f"GB小时累计: {'未知 (无资源计量)' if _csum['total_mem_gb_hours'] == 0 and _csum['count'] > 0 else _csum['total_mem_gb_hours']} (限18000)",
                       f"$0账单快照: {_csum['bill_snapshots']}件 (来源 state/m5-20261105/cost.json)"]
            # Item 6 passes only with a real $0 bill snapshot AND known
            # resource totals; free calls alone (cost=0, resources unknown)
            # do not prove the quotas. Absence stays 缺数据, never assumed 0.
            cost_known = bool(_csum["bill_snapshots"] > 0 and _csum["count"] > 0)
        except Exception:
            cost_ev = []
    items["6"] = item("缺数据" if not cost_known else "通过", 0, 0,
                      [f"窗口内成本/账单产物={len(cost_files)} 件"] +
                      ([f"  {x}" for x in cost_files] if cost_files else []) + cost_ev,
                      [] if cost_known else ["无OCPU小时累计vs 3000限、GB小时累计vs 18000限、$0账单快照",
                       "仅有OCI P95用量文件 (附件, 非成本证据)"],
                      "未发现超支证据, 但成本为0无证据, 不得判通过" if not cost_known else "成本库有记录且有$0账单快照")

    # ---- P0 unhealed flag (M5 gate: no unhealed P0 in the window) ----
    p0 = []
    for p in ("state/self-health-latest.json", "state/butler-self-health-latest.json"):
        d = load_json(os.path.join(BASE, p))
        if isinstance(d, dict) and d.get("alert") is True:
            p0.append(f"{p} alert=true (score {d.get('score')})")
    # ---- OCI 7d P95 attachment (not an autonomy item) ----
    oci = load_json(os.path.join(BASE, "state", "oci-7d-p95-latest.json"))
    oci_note = "无OCI P95文件"
    oci_age = None
    if isinstance(oci, dict):
        g = parse_ts(oci.get("generated_at_utc"))
        if g is not None:
            oci_age = (end - g).total_seconds() / 3600.0
        inst = []
        for i in oci.get("instances", []):
            inst.append("%s CPU p95 %.1f%% / 内存 p95 %.1f%% / 网络 p95 %.2f%%, idle_risk=%s"
                        % (i.get("name"), i.get("cpu_p95", -1),
                           i.get("memory_p95", -1),
                           i.get("network_p95_pct", -1), i.get("idle_risk")))
        oci_note = ("生成于 %s (窗口结束前 %.1f小时); any_idle_risk=%s; %s"
                    % (oci.get("generated_at_utc"), oci_age or -1,
                       oci.get("any_idle_risk"), "; ".join(inst)))

    passed = sum(1 for v in items.values() if v["verdict"] == "通过")
    result = {
        "window": {"start": start.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                   "end": end.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                   "label": label},
        "items": items,
        "passed": passed, "total": 6,
        "window_pass": passed >= 6,
        "p0_unhealed": p0,
        "oci_p95_attachment": oci_note,
        "m5_rule": "连续7个24h窗口中>=6个同时满足6项且无P0未自愈; 7天未跑满前不得称M5已过关",
        "generated_at": datetime.now(CST).strftime("%Y-%m-%dT%H:%M:%S+08:00"),
    }
    os.makedirs(args.out_dir, exist_ok=True)
    jpath = os.path.join(args.out_dir, f"window-{label}.json")
    mpath = os.path.join(args.out_dir, f"window-{label}.md")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    lines = [f"# M5 验收表 窗口 {start.strftime('%Y-%m-%d %H:%M')} – {end.strftime('%Y-%m-%d %H:%M')} CST",
             "",
             f"结论: **{passed}/6 项通过**; 窗口通过(6/6): {'是' if passed >= 6 else '否'}; "
             f"P0未自愈: {len(p0)} 件 ({'; '.join(p0) or '无'})",
             "",
             "| # | 验收项 | 结果 | 成功数/总数 | 证据 / 缺口 |",
             "|---|---|---|---|---|"]
    names = {"1": "非L3人工介入0", "2": "经验收交付>=1件/日",
             "3": "假DONE 0、对账平", "4": "无主项0、最老pending<30分钟、默认动作100%",
             "5": "Tier-1全窗口>=2、降级不丢任务有恢复记录",
             "6": "成本0、不超Oracle额度"}
    for k in ("1", "2", "3", "4", "5", "6"):
        v = items[k]
        ev = "<br>".join(v["evidence"] + (["缺: " + x for x in v["missing"]] if v["missing"] else []))
        lines.append(f"| {k} | {names[k]} | {v['verdict']} | {v['success']}/{v['total']} | {ev} |")
        if v["note"]:
            lines.append(f"|  | 注: {v['note']} |  |  |  |")
    lines += ["",
              "## OCI 7天P95附件 (必要附件, 不计入6项)",
              "",
              oci_note,
              "",
              "## M5规则",
              "",
              "连续7个24小时窗口, 其中>=6个同时满足6项且无P0未自愈, 方可申报M5; "
              "7天窗口未跑满前不得称M5已过关。本窗口为第1个窗口。",
              "",
              f"生成: {result['generated_at']} CST; 脚本: scripts/m5-window-check-20261105.py; "
              f"JSON: state/m5-20261105/window-{label}.json"]
    with open(mpath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"window {label}: {passed}/6 pass; wrote {mpath} and {jpath}")
    return min(passed and 0 or 6 - passed, 6) if passed < 6 else 0


if __name__ == "__main__":
    sys.exit(main())
