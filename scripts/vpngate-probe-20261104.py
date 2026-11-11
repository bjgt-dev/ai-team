#!/usr/bin/env python3
"""Minimal VPN Gate probe (2026-11-04, replaces lost sni-based health script).
Fetches public VPN Gate API, counts nodes by country, TCP-probes top JP/KR nodes on 443/1194.
No SSTP handshake, no residential check - TCP reachable != residential, != stable.
Writes JSON to state/vpngate-probe-latest.json, prints summary only.
"""
import urllib.request, csv, collections, socket, json, datetime
req = urllib.request.Request("https://www.vpngate.net/api/iphone/", headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=30) as r:
    text = r.read().decode("utf-8", errors="ignore")
lines = []
for l in text.splitlines():
    if l.startswith("*"):
        continue
    if l.startswith("#HostName"):
        lines.append(l[1:])
    elif l.startswith("#"):
        continue
    else:
        lines.append(l)
rows = list(csv.DictReader(lines))
by = collections.Counter(r["CountryShort"] for r in rows)
def probe(ip, port):
    try:
        s = socket.create_connection((ip, port), timeout=5); s.close(); return True
    except Exception:
        return False
rows_sorted = sorted(rows, key=lambda r: int(r["Score"] or 0), reverse=True)
samples = []
seen = {}
for r in rows_sorted:
    cc = r["CountryShort"]
    if cc in ("JP", "KR") and seen.get(cc, 0) < 2:
        seen[cc] = seen.get(cc, 0) + 1
        samples.append({"country": cc, "ip": r["IP"], "score": r["Score"], "tcp1194": probe(r["IP"], 1194), "tcp443": probe(r["IP"], 443)})
out = {"ts": datetime.datetime.now().isoformat(), "total": len(rows), "by_country": dict(by), "samples": samples}
with open("/home/ubuntu/ai-team/state/vpngate-probe-latest.json", "w") as f:
    json.dump(out, f, indent=2)
print(json.dumps(out, indent=2))
