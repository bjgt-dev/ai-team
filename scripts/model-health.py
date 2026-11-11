#!/usr/bin/env python3
# model-health.py: gateway model health registry + router.
# Data: /home/ubuntu/ai-team/state/model-health.json (hub).
# Commands: probe | probe-cooldown | route --capability X | record-success M | record-failure M [error]
# Capabilities are labeled from P1 probes + loop experience (NOT a C1 rerun):
#   nemotron-3-ultra-550b-a55b:free -> long_doc, reasoning, code, short_doc, simple_query
#   free-auto -> routing (matches any capability)
#   glm-4-flash-cc -> short_doc, simple_query
# Other models: status recorded by probe, capabilities [] until labeled.
import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta

BASE = "/home/ubuntu/ai-team"
STATE_PATH = os.path.join(BASE, "state/model-health.json")
OPENCODFIG = "/home/ubuntu/.config/opencode/opencode.json"
GATEWAY = "http://127.0.0.1:3001/v1"
CAPS = {
    "nvidia/nemotron-3-ultra-550b-a55b:free": ["long_doc", "reasoning", "code", "short_doc", "simple_query"],
    "free-auto": ["routing"],
    "glm-4-flash-cc": ["short_doc", "simple_query"],
    # gemini-3.5-flash-lite: tier_a, repeated canary 3/3 incl. 2026-11-05 16:16; same evidence standard as glm-4-flash-cc
    "gemini-3.5-flash-lite": ["short_doc", "simple_query"],
}
COOLDOWN_H = {1: 1, 2: 4}


def now_s():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def load():
    if os.path.isfile(STATE_PATH):
        try:
            d = json.load(open(STATE_PATH))
            if isinstance(d, dict) and "models" in d:
                return d
        except Exception:
            pass
    return {"last_full_probe": "", "models": {}}


def save(d):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def token():
    try:
        c = json.load(open(OPENCODFIG))
        for p in c.get("provider", {}).values():
            if p.get("apiKey"):
                return p["apiKey"]
            opt = p.get("options") or {}
            if opt.get("apiKey"):
                return opt["apiKey"]
        mg = c.get("provider", {}).get("my-gateway", {})
        return mg.get("apiKey", "") or (mg.get("options") or {}).get("apiKey", "")
    except Exception:
        return ""


def http_json(method, url, body=None, timeout=30):
    req = urllib.request.Request(url, method=method)
    t = token()
    if t:
        req.add_header("Authorization", "Bearer " + t)
    req.add_header("Content-Type", "application/json")
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def probe_one(mid):
    # M9-TRACE-P2: one trace line per probe (never breaks probe).
    _t0 = datetime.now()
    _http = None; _err = ""; _to = False; _ok = False
    try:
        d = http_json("POST", GATEWAY + "/chat/completions", {"model": mid, "messages": [{"role": "user", "content": "只回 OK"}], "max_tokens": 5}, timeout=30)
        ch = (d.get("choices") or [])
        if ch and (ch[0].get("message") or {}).get("content") is not None:
            _ok = True
        else:
            _err = "no choices"
        _http = 200
    except Exception as e:
        _http = getattr(e, "code", None)
        _err = str(e)[:200]
        _to = "timed out" in _err.lower()
    try:
        import sys as _sys
        _sys.path.insert(0, "/home/ubuntu/ai-team/scripts")
        import trace_20261105 as _trace
        _trace.emit(task_id="modelhealth-probe-" + mid.replace("/", "-").replace(":", "-") + "-" + datetime.now().strftime("%Y%m%d-%H%M%S"),
                    model_id=mid, latency_ms=int(round((datetime.now() - _t0).total_seconds() * 1000)),
                    source="model-health-probe", http_status=_http,
                    returned_model="", timeout_s=30, content="OK" if _ok else "",
                    error=_err, timed_out=_to)
    except Exception:
        pass
    return _ok, _err


def apply_result(d, mid, ok, err=""):
    m = d["models"].get(mid) or {"status": "untested", "capabilities": CAPS.get(mid, []), "consecutive_fails": 0, "last_ok": "", "last_fail": "", "last_error": "", "cooldown_until": "", "avg_latency_ms": 0}
    if mid in CAPS and not m.get("capabilities"):
        m["capabilities"] = CAPS[mid]
    if ok:
        m["status"] = "healthy"
        m["consecutive_fails"] = 0
        m["last_ok"] = now_s()
        m["cooldown_until"] = ""
        m["last_error"] = ""
    else:
        m["consecutive_fails"] = (m.get("consecutive_fails") or 0) + 1
        m["last_fail"] = now_s()
        m["last_error"] = err
        h = COOLDOWN_H.get(m["consecutive_fails"], 24)
        m["cooldown_until"] = (datetime.now() + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S")
        m["status"] = "unhealthy"
    d["models"][mid] = m


# 2026-11-05 layers: ghost alias removed from routing registry; probe must not re-add it.
# reviewer-primary is a newapi channel alias (channels 3/4/5/8 map it to different upstreams),
# not a real model. It stays in newapi until channel cleanup; it must never re-enter model-health.
BLOCKLIST = {"reviewer-primary"}


def cmd_probe(a):
    d = load()
    try:
        ml = http_json("GET", GATEWAY + "/models", timeout=30)
        ids = [x.get("id") for x in (ml.get("data") or []) if x.get("id")]
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)[:200]}, ensure_ascii=False))
        return
    from concurrent.futures import ThreadPoolExecutor
    def _probe(mid):
        return mid, probe_one(mid)
    _results = {}
    with ThreadPoolExecutor(max_workers=8) as _ex:
        for _mid, _res in _ex.map(_probe, ids):
            _results[_mid] = _res
    ids = [m for m in ids if m not in BLOCKLIST]
    for mid in ids:
        ok, err = _results[mid]
        apply_result(d, mid, ok, err)
        print("probe %s %s" % (mid, "OK" if ok else "FAIL " + err[:80]), flush=True)
    d["last_full_probe"] = now_s()
    n = len(d["models"])
    nh = sum(1 for m in d["models"].values() if m.get("status") == "healthy")
    # M3-4 (2026-11-07 落地扫描): healthy_ratio 只印 stdout 未落盘, 回执称文件为源不符;
    # 改为先写盘再打印, 打印口径不变, 仅新增一个顶层键.
    d["healthy_ratio"] = "%d/%d" % (nh, n)
    save(d)
    print(json.dumps({"status": "ok", "total_models_discovered": n, "healthy": nh, "unhealthy": n - nh, "healthy_ratio": "%d/%d" % (nh, n)}, ensure_ascii=False))


def cmd_probe_cooldown(a):
    d = load()
    now = datetime.now()
    done = 0
    for mid, m in d["models"].items():
        cu = m.get("cooldown_until") or ""
        if cu and datetime.strptime(cu, "%Y-%m-%dT%H:%M:%S") <= now:
            ok, err = probe_one(mid)
            apply_result(d, mid, ok, err)
            done += 1
            print("cooldown-probe %s %s" % (mid, "OK" if ok else "FAIL"), flush=True)
    save(d)
    print(json.dumps({"status": "ok", "probed": done}, ensure_ascii=False))


def cmd_route(a):
    d = load()
    now = datetime.now()
    out = []
    unlabeled = []
    for mid, m in d["models"].items():
        if m.get("status") != "healthy":
            continue
        cu = m.get("cooldown_until") or ""
        if cu and datetime.strptime(cu, "%Y-%m-%dT%H:%M:%S") > now:
            continue
        caps = m.get("capabilities") or []
        if a.capability in caps or "routing" in caps:
            out.append((m.get("avg_latency_ms") or 0, -len(caps), mid))
        elif not caps:
            # 2026-11-05 layers: UNTESTED (capabilities empty) is NEVER in production route.
            # It is listed separately and only returned when --include-untested is passed (test traffic only).
            unlabeled.append((m.get("avg_latency_ms") or 0, mid))
    out.sort()
    unlabeled.sort()
    res = {"models": [x[2] for x in out], "untested": [mid for _, mid in unlabeled],
           "untested_included": bool(getattr(a, "include_untested", False))}
    if getattr(a, "include_untested", False):
        res["models"] = res["models"] + res["untested"]
    print(json.dumps(res, ensure_ascii=False))


POOL_PATH = os.path.join(BASE, "state/gateway-pool.json")
RR_PATH = os.path.join(BASE, "state/gateway-layers-20261105/rr-state.json")
SUPPLIER = {
    "glm-4-flash-cc": "Zhipu", "glm-4-flash": "Zhipu", "glm-4.7-flash": "Zhipu", "probe-zhipu-flash": "Zhipu",
    "gemini-3.5-flash-lite": "Google", "gemini-3-flash-preview": "Google", "gemini-3.5-flash": "Google",
    "gemini-3.6-flash": "Google", "gemini-3.8-flash": "Google", "gemini-3.8-flash-lite": "Google",
    "agnes-3.0-flash": "Agnes",
    "qwen3.8-27b": "Alibaba", "qwen3.8-flash": "Alibaba", "qwen3.8-max-0902": "Alibaba",
    "nvidia/nemotron-3-ultra-550b-a55b:free": "OpenRouter", "llama-4-scout": "Cloudflare",
    "Atria-Dawn": "Atria", "intern-s2": "Atria", "Agents-A1": "Atria", "Sage-A1.5": "Atria",
}

def _pool():
    return json.load(open(POOL_PATH))

def _rr_pick(key, candidates):
    os.makedirs(os.path.dirname(RR_PATH), exist_ok=True)
    st = json.load(open(RR_PATH)) if os.path.isfile(RR_PATH) else {}
    idx = int(st.get(key, 0))
    # weighted round-robin for LIGHT 5:5 / MEDIUM 70:30 is realised by the caller's
    # --pick sequence; pointer advances one slot per pick within the candidate list.
    pick = candidates[idx % len(candidates)] if candidates else None
    st[key] = idx + 1
    json.dump(st, open(RR_PATH, "w"), ensure_ascii=False, indent=2)
    return pick

def cmd_route_layer(a):
    pool = _pool()
    layers = pool.get("layers", {})
    layer = layers.get(a.layer)
    if not layer:
        print(json.dumps({"status": "error", "error": "unknown layer " + a.layer}, ensure_ascii=False)); return
    d = load()
    now = datetime.now()
    def alive(mid):
        m = d["models"].get(mid)
        if not m or m.get("status") != "healthy":
            return False
        cu = m.get("cooldown_until") or ""
        return not (cu and datetime.strptime(cu, "%Y-%m-%dT%H:%M:%S") > now)
    def tested(mid):
        return bool((d["models"].get(mid) or {}).get("capabilities"))
    cands, untested, dead = [], [], []
    if a.layer == "LIGHT":
        pool_list = list(layer.get("primary_pool", []))
        fallback = ["agnes-3.0-flash"]
    elif a.layer == "MEDIUM":
        # 2026-11-05 bailian: MEDIUM primary_pool is the production pool once
        # non-empty (qwen3.8-27b promoted); fall back to test_pool only while
        # primary_pool is empty, so healthy+tested test models stay routable.
        pool_list = list(layer.get("primary_pool") or layer.get("test_pool", []))
        fallback = ["glm-4-flash-cc"]
    elif a.layer == "COMPLEX":
        pool_list = list(layer.get("fallback_pool", []))
        fallback = []
    else:  # REASONING
        pool_list = ["gemini-3.5-flash-lite", "gemini-3-flash-preview", "glm-4-flash-cc"]
        fallback = []
    for mid in pool_list:
        if not alive(mid):
            dead.append(mid)
        elif not tested(mid) and not a.test:
            untested.append(mid)
        else:
            cands.append(mid)
    if a.layer == "REASONING" and a.producer_supplier:
        # cross-supplier iron rule: reviewer must be a different supplier than the producer
        cands = [m for m in cands if SUPPLIER.get(m, "?") != a.producer_supplier]
    out = {"status": "ok", "layer": a.layer, "candidates": cands,
           "pool_ratio": "%d/%d" % (len(cands), len(pool_list)),
           "untested_excluded": untested, "unhealthy_excluded": dead,
           "fallback": fallback, "test_mode": bool(a.test)}
    if a.pick:
        out["pick"] = _rr_pick("layer:" + a.layer, cands) if cands else None
        out["pick_fallback"] = fallback[0] if (not cands and fallback) else None
    print(json.dumps(out, ensure_ascii=False))


def _revive_ping(mid):
    # Module-level (was nested in cmd_revive) so one model can be probed
    # directly for trace proof without running the daily revive (which would
    # move revive streaks). Same logic as the old nested _ping + M9-TRACE-P2 emit.
    _t0 = datetime.now()
    _http = 200; _err = ""; _to = False; _txt = ""; _ok = False
    try:
        dd = http_json("POST", GATEWAY + "/chat/completions",
                       {"model": mid, "messages": [{"role": "user", "content": "1+1=? 只回数字"}], "max_tokens": 5}, timeout=20)
        ch = (dd.get("choices") or [])
        _txt = ((ch[0].get("message") or {}).get("content") or "").strip()
        _ok = ("2" in _txt)
    except Exception as _e:
        _http = getattr(_e, "code", None)
        _err = str(_e)[:200]
        _to = "timed out" in _err.lower()
    try:
        import sys as _sys
        _sys.path.insert(0, "/home/ubuntu/ai-team/scripts")
        import trace_20261105 as _trace
        _trace.emit(task_id="modelhealth-revive-" + mid.replace("/", "-").replace(":", "-") + "-" + datetime.now().strftime("%Y%m%d-%H%M%S"),
                    model_id=mid, latency_ms=int(round((datetime.now() - _t0).total_seconds() * 1000)),
                    source="model-health-revive", http_status=_http,
                    returned_model="", timeout_s=20, content=_txt if _ok else "",
                    error=_err, timed_out=_to)
    except Exception:
        pass
    return _ok


def cmd_revive(a):
    """Daily canary revival: light ping (1+1=2) on every unhealthy model.
    revive_streak>=3 -> status healthy, cooldown cleared, logged as promotion.
    Never auto-promotes to tier_a/tier_b; promoted models only re-enter tier_x standby."""
    d = load()
    logp = os.path.join(BASE, "state/gateway-layers-20261105/revive-log.jsonl")
    os.makedirs(os.path.dirname(logp), exist_ok=True)
    from concurrent.futures import ThreadPoolExecutor
    bad = [mid for mid, m in d["models"].items() if m.get("status") != "healthy"]
    def _ping(mid):
        return mid, _revive_ping(mid)
    res = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for mid, ok in ex.map(_ping, bad):
            res[mid] = ok
    promoted = []
    for mid, ok in res.items():
        m = d["models"][mid]
        if ok:
            m["revive_streak"] = (m.get("revive_streak") or 0) + 1
            m["last_ok"] = now_s()
            if m["revive_streak"] >= 3:
                m["status"] = "healthy"; m["consecutive_fails"] = 0; m["cooldown_until"] = ""
                promoted.append(mid)
        else:
            m["revive_streak"] = 0
            m["consecutive_fails"] = (m.get("consecutive_fails") or 0) + 1
            m["last_fail"] = now_s()
    save(d)
    _ok = sum(1 for v in res.values() if v)
    rec = {"ts": now_s(), "probed": len(bad), "ok": _ok, "ok_ratio": "%d/%d" % (_ok, len(bad)),
           "promoted": promoted, "promoted_ratio": "%d/%d" % (len(promoted), len(bad)), "detail": res}
    with open(logp, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(rec, ensure_ascii=False))


def cmd_record_caps(a):
    d = load()
    m = d["models"].get(a.model)
    if not m:
        print(json.dumps({"status": "error", "error": "unknown model"}, ensure_ascii=False)); return
    caps = [c.strip() for c in a.caps.split(",") if c.strip()]
    m["capabilities"] = caps
    m["caps_evidence"] = a.evidence
    m["caps_updated"] = now_s()
    save(d)
    print(json.dumps({"status": "ok", "model": a.model, "capabilities": caps}, ensure_ascii=False))


def cmd_record(a, ok):
    d = load()
    apply_result(d, a.model, ok, getattr(a, "error", "") or "")  # 2026-11-05 fix: record-success has no --error arg; old code crashed
    save(d)
    print(json.dumps({"status": "ok", "model": a.model, "ok": ok}, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe")
    sub.add_parser("probe-cooldown")
    r = sub.add_parser("route")
    r.add_argument("--capability", required=True)
    r.add_argument("--include-untested", action="store_true",
                   help="test traffic only: also return UNTESTED (capabilities empty) models")
    rl = sub.add_parser("route-layer")
    rl.add_argument("--layer", required=True, choices=["LIGHT", "MEDIUM", "COMPLEX", "REASONING"])
    rl.add_argument("--pick", action="store_true", help="return one model via round-robin pointer")
    rl.add_argument("--test", action="store_true", help="test traffic: allow UNTESTED pool members")
    rl.add_argument("--producer-supplier", default="", help="REASONING: producer supplier to exclude (cross-supplier rule)")
    sub.add_parser("revive")
    rc = sub.add_parser("record-caps")
    rc.add_argument("--model", required=True)
    rc.add_argument("--caps", required=True)
    rc.add_argument("--evidence", required=True)
    s = sub.add_parser("record-success")
    s.add_argument("model")
    f = sub.add_parser("record-failure")
    f.add_argument("model")
    f.add_argument("error", nargs="?", default="")
    a = ap.parse_args()
    if a.cmd == "route-layer":
        cmd_route_layer(a)
    elif a.cmd == "revive":
        cmd_revive(a)
    elif a.cmd == "record-caps":
        cmd_record_caps(a)
    elif a.cmd == "probe":
        cmd_probe(a)
    elif a.cmd == "probe-cooldown":
        cmd_probe_cooldown(a)
    elif a.cmd == "route":
        cmd_route(a)
    elif a.cmd == "record-success":
        cmd_record(a, True)
    elif a.cmd == "record-failure":
        cmd_record(a, False)


if __name__ == "__main__":
    main()
