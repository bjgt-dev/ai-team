#!/usr/bin/env python3
"""OpenRouter multi-account rotation guard (2026-11-05).

Reads OpenRouter channel keys from the newapi DB into memory only (never
prints or logs a key; only the last 4 chars are recorded). Per account:
  GET https://openrouter.ai/api/v1/key -> free_model_daily_requests
  remaining == 0  -> disable that channel (status 0), newapi skips it
  remaining > 0 and channel was guard-disabled -> re-enable (status 1)
Writes state/openrouter-rotation-20261105/rotation-pointer.json.
No other channel is touched. No money spent, no new account created.
"""
import json, os, subprocess, time, urllib.request, urllib.error

BASE = "/home/ubuntu/ai-team"
STATE_DIR = os.path.join(BASE, "state/openrouter-rotation-20261105")
POINTER = os.path.join(STATE_DIR, "rotation-pointer.json")
CHANNEL_IDS = (15, 16, 17)

def mysql(sql, write=False):
    with open("/tmp/or_guard_q.sql", "w") as f:
        f.write(sql)
    with open("/tmp/or_guard_q.sql") as fin:
        out = subprocess.run(["docker", "exec", "-i", "ai-stack-new-api-db-1", "sh", "-c",
            "mysql -uroot -p\"$MYSQL_ROOT_PASSWORD\" -N oneapi"],
            stdin=fin, capture_output=True, text=True, timeout=60)
    return out.stdout.strip()

def key_quota(key):
    req = urllib.request.Request("https://openrouter.ai/api/v1/key",
                                 headers={"Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.load(r)["data"]
    fm = d.get("free_model_daily_requests") or {}
    return {"used": fm.get("used"), "limit": fm.get("limit"),
            "remaining": fm.get("remaining"),
            "funded_10usd": (fm.get("limit") == 1000),
            "is_free_tier": d.get("is_free_tier")}

def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    prev = {}
    if os.path.exists(POINTER):
        try:
            prev = json.load(open(POINTER))
        except Exception:
            prev = {}
    guard_disabled = set(prev.get("guard_disabled_channel_ids") or [])
    rows = mysql("SELECT id, name, status, `key` FROM channels WHERE id IN (15,16,17) ORDER BY id;")
    accounts, actions = [], []
    for line in rows.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        cid, name, status, key = int(parts[0]), parts[1], int(parts[2]), parts[3]
        try:
            q = key_quota(key)
        except Exception as e:
            accounts.append({"account": name, "channel_id": cid, "key_last4": key[-4:],
                             "status": "quota_check_failed", "error": str(e)[:120],
                             "db_status": status})
            continue
        remaining = q["remaining"]
        if remaining == 0 and status == 1:
            mysql("UPDATE channels SET status=0 WHERE id=%d;" % cid)
            guard_disabled.add(cid)
            actions.append("disabled channel %d (%s): daily quota exhausted" % (cid, name))
            status = 0
        elif remaining and remaining > 0 and cid in guard_disabled and status == 0:
            mysql("UPDATE channels SET status=1 WHERE id=%d;" % cid)
            guard_disabled.discard(cid)
            actions.append("re-enabled channel %d (%s): quota restored, remaining=%s" % (cid, name, remaining))
            status = 1
        accounts.append({"account": name, "channel_id": cid, "key_last4": key[-4:],
                         "limit": q["limit"], "used": q["used"], "remaining": remaining,
                         "funded_10usd": q["funded_10usd"], "db_status": status,
                         "status": "active" if remaining and remaining > 0 else "exhausted"})
        del key
    cur = next((i for i, a in enumerate(accounts) if a.get("remaining")), -1)
    pointer = {"updated_at_cst": time.strftime("%Y-%m-%d %H:%M:%S"),
               "next_reset_utc": "2026-11-06T00:00:00Z",
               "current_account_index": cur,
               "guard_disabled_channel_ids": sorted(guard_disabled),
               "rule": "per-account round-robin via newapi peer channels (priority 0, weight 0, identical model list); guard disables a channel at remaining==0 and re-enables after UTC reset; 404/503 are model issues, never trigger an account switch; openrouter/free random router is not in the production route",
               "accounts": accounts,
               "actions_this_run": actions}
    with open(POINTER, "w") as f:
        json.dump(pointer, f, ensure_ascii=False, indent=2)
    print(json.dumps(pointer, ensure_ascii=False))

if __name__ == "__main__":
    main()
