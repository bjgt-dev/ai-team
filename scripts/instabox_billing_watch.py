#!/usr/bin/env python3
"""InstaBox billing/free-tier watch (hub-local, daily cron).
Baselines and diffs: public pricing page hash + account #1 machines snapshot
+ any usage/billing endpoint that answers. Alerts appended to alerts.log.
Chairman order 2026-10-21: free tier may start charging at any time; watch it.
"""
import hashlib, json, os, urllib.request, datetime

BASE = "/home/ubuntu/ai-team/state/instabox-watch"
os.makedirs(BASE, exist_ok=True)
KEY = open("/home/ubuntu/ai-team/state/.instabox-api-key").read().strip()
NOW = datetime.datetime.now().isoformat(timespec="seconds")

def get(url, auth=False, timeout=25, key=None):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + (key or KEY)} if auth else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        body = ""
        try: body = e.read().decode("utf-8", "replace")[:300]
        except Exception: pass
        return getattr(e, "code", 0), body or str(e)

def alert(msg):
    with open(os.path.join(BASE, "alerts.log"), "a") as f:
        f.write(f"{NOW} {msg}\n")

snap = {"at": NOW}
# 1) pricing page hash
st, body = get("https://instabox.sh/pricing")
snap["pricing_status"] = st
import re as _re
_txt = _re.sub(r"<script.*?</script>|<style.*?</style>", " ", body, flags=_re.S)
_txt = _re.sub(r"<[^>]+>", " ", _txt)
_txt = _re.sub(r"\s+", " ", _txt).strip()
snap["pricing_hash"] = hashlib.sha256(_txt.encode()).hexdigest()[:16] if st == 200 else None
# 2) machines snapshot
st, body = get("https://api.instabox.sh/v1/machines", auth=True)
snap["machines_status"] = st
try:
    ms = json.loads(body)
    items = ms.get("data") if isinstance(ms, dict) else None
    if items is None and isinstance(ms, dict): items = ms.get("machines")
    if items is None: items = ms
    snap["machines"] = [[m.get("id"), m.get("name"), m.get("state") or m.get("status")] for m in items] if isinstance(items, list) else str(ms)[:200]
except Exception:
    snap["machines"] = body[:200]
# 2b) machines snapshot, account #2 (key minted 2026-10-21)
KEY2_PATH = "/home/ubuntu/ai-team/state/.instabox2-api-key"
if os.path.exists(KEY2_PATH):
    KEY2 = open(KEY2_PATH).read().strip()
    st, body = get("https://api.instabox.sh/v1/machines", auth=True, key=KEY2)
    snap["machines2_status"] = st
    try:
        ms = json.loads(body)
        items = ms.get("data") if isinstance(ms, dict) else None
        if items is None and isinstance(ms, dict): items = ms.get("machines")
        if items is None: items = ms
        snap["machines2"] = [[m.get("id"), m.get("name"), m.get("state") or m.get("status")] for m in items] if isinstance(items, list) else str(ms)[:200]
    except Exception:
        snap["machines2"] = body[:200]
# 3) usage/billing probes (record whatever answers)
for name, path in [("usage", "/v1/usage"), ("account", "/v1/account"), ("billing", "/v1/billing")]:
    st, body = get("https://api.instabox.sh" + path, auth=True)
    if st and st < 404:
        snap[name] = {"status": st, "body": body[:500], "hash": hashlib.sha256(body.encode()).hexdigest()[:16]}

prev_path = os.path.join(BASE, "latest.json")
prev = None
if os.path.exists(prev_path):
    try: prev = json.load(open(prev_path))
    except Exception: pass
if prev:
    if prev.get("pricing_hash") and snap["pricing_hash"] and prev["pricing_hash"] != snap["pricing_hash"]:
        alert(f"PRICING PAGE CHANGED hash {prev['pricing_hash']} -> {snap['pricing_hash']}")
    if prev.get("machines") != snap.get("machines"):
        alert(f"MACHINES CHANGED {prev.get('machines')} -> {snap.get('machines')}")
    if "machines2" in prev and prev.get("machines2") != snap.get("machines2"):
        alert(f"MACHINES2 CHANGED {prev.get('machines2')} -> {snap.get('machines2')}")
    for name in ("usage", "account", "billing"):
        ph = (prev.get(name) or {}).get("hash")
        ch = (snap.get(name) or {}).get("hash")
        if ph and ch and ph != ch:
            alert(f"{name.upper()} CHANGED hash {ph} -> {ch} body={snap[name]['body'][:200]}")
json.dump(snap, open(prev_path, "w"), indent=1, ensure_ascii=False)
print(json.dumps(snap, ensure_ascii=False)[:600])
