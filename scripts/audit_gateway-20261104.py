#!/usr/bin/env python3
"""ORDER13 audit_gateway (daily): upstream + channels (read-only SQL, allowed
columns only, never key) + seat + newapi logs by model/day.
Schema note (actual vs order assumption): logs.content is EMPTY for successful
rows (3990/3992 empty on 2026-11-03+), so content-nonempty is reported as
measured (near 0) and quota>0 is used as the success proxy. Channels table has
no separate 'type' semantics beyond the integer type column; it is reported as-is.
Output: state/gateway-audit.json; diff vs previous audit (if any) printed as JSON."""
import json, os, subprocess, urllib.request
BASE = "/home/ubuntu/ai-team"
OUT = os.path.join(BASE, "state/gateway-audit.json")
cfg = json.load(open("/home/ubuntu/.config/opencode/opencode.json"))
tok = ""
for pr in cfg.get("provider", {}).values():
    opt = pr.get("options") or {}
    if opt.get("apiKey"):
        tok = opt["apiKey"]; break
req = urllib.request.Request("http://127.0.0.1:3001/v1/models",
    headers={"Authorization": "Bearer " + tok})
try:
    d = json.load(urllib.request.urlopen(req, timeout=30))
    upstream = sorted([x.get("id") for x in d.get("data") or [] if x.get("id")])
except Exception as e:
    upstream = ["ERROR:" + str(e)[:200]]
seat = sorted((cfg.get("provider", {}).get("my-gateway", {}).get("models") or {}).keys())

def mysql(sql):
    # Feed SQL via stdin to avoid shell quoting issues; password is expanded
    # inside the container only and never printed.
    with open("/tmp/audit_q.sql", "w") as f:
        f.write(sql)
    with open("/tmp/audit_q.sql") as fin:
        out = subprocess.run(["docker", "exec", "-i", "ai-stack-new-api-db-1", "sh", "-c",
            'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N oneapi 2>/dev/null'],
            stdin=fin, capture_output=True, text=True, timeout=60)
    return out.stdout.strip()
ch_raw = mysql("SELECT id, name, type, status, priority, weight, `group`, models FROM channels ORDER BY id;")
channels = []
for line in ch_raw.splitlines():
    parts = line.split("\t")
    if len(parts) >= 8:
        channels.append({"id": parts[0], "name": parts[1], "type": parts[2], "status": parts[3],
                         "priority": parts[4], "weight": parts[5], "group": parts[6],
                         "models": parts[7]})
usage_raw = mysql("SELECT model_name, DATE(FROM_UNIXTIME(created_at)), COUNT(*), SUM(quota>0), SUM(content IS NOT NULL AND content<>'') FROM logs WHERE created_at >= UNIX_TIMESTAMP('2026-11-01') GROUP BY model_name, DATE(FROM_UNIXTIME(created_at)) ORDER BY 2, 3 DESC;")
usage = []
for line in usage_raw.splitlines():
    parts = line.split("\t")
    if len(parts) >= 5:
        usage.append({"model": parts[0], "day": parts[1], "calls": int(parts[2]),
                      "quota_positive": int(parts[3]), "content_nonempty": int(parts[4])})
prev = None
if os.path.exists(OUT):
    try:
        prev = json.load(open(OUT))
    except Exception:
        prev = None
audit = {"date": __import__("time").strftime("%Y-%m-%d"), "upstream_count": len(upstream),
         "upstream": upstream, "seat": seat, "channels": channels, "usage_since_2026_11_01": usage,
         "schema_note": "logs.content empty for successful rows; quota>0 used as success proxy; channels key column never selected."}
diff = {}
if prev:
    diff = {"upstream_added": sorted(set(upstream) - set(prev.get("upstream", []))),
            "upstream_removed": sorted(set(prev.get("upstream", [])) - set(upstream)),
            "seat_added": sorted(set(seat) - set(prev.get("seat", []))),
            "seat_removed": sorted(set(prev.get("seat", [])) - set(seat))}
else:
    diff = {"note": "first audit, baseline established"}
audit["diff_vs_previous"] = diff
json.dump(audit, open(OUT, "w"), ensure_ascii=False, indent=2)
print(json.dumps({"upstream_count": len(upstream), "seat_count": len(seat),
                  "channels": len(channels), "usage_rows": len(usage), "diff": diff}, ensure_ascii=False))
