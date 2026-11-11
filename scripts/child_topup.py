import json, os, re, subprocess, urllib.request

BASE = "http://127.0.0.1:3001/v1"
KEY = os.environ["NEWAPI_KEY"]
RECORDS = open("/home/ubuntu/ai-team/knowledge/child-agent/session-records-202610.md").read()

def psql(sql):
    r = subprocess.run(["docker", "exec", "-i", "ops-pg", "psql", "-U", "postgres", "-d", "child_agent", "-At", "-c", sql], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:300])
    return r.stdout.strip()

existing = psql("SELECT occurred_at || chr(124) || verb || chr(124) || object_id FROM learning_events ORDER BY occurred_at")
have = set()
for row in psql("SELECT occurred_at || chr(124) || coalesce(object_id, chr(32)) FROM learning_events").splitlines():
    if "|" in row:
        d, o = row.split("|", 1)
        have.add((d.strip(), o.strip()))

prompt = ("你是学习事件抽取器。只输出 JSON 数组。字段：date(YYYY-MM-DD)、verb(英文动词短语)、object(对象)、domain(能力领域中文)、result(中文简述)、confidence(0-1)、tag。"
          "tag 铁律：明确发生的具体行为/事实=ai_observed；概括判断=ai_inferred；绝不许 parent_confirmed。"
          "每场会话抽 3-6 条。下面是已入库事件清单，与之同日期同对象的不要重复输出。\n\n【已入库】\n" + existing + "\n\n【会话档案】\n" + RECORDS)
body = json.dumps({"model": "foreman-prime", "messages": [{"role": "user", "content": prompt}], "max_tokens": 8000}).encode()
req = urllib.request.Request(BASE + "/chat/completions", data=body, headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
txt = json.loads(urllib.request.urlopen(req, timeout=600).read())["choices"][0]["message"].get("content") or ""
txt = re.sub(r"^```(json)?|```$", "", txt.strip(), flags=re.M).strip()
events = json.loads(txt[txt.find("["):txt.rfind("]") + 1])

def esc(s):
    return str(s).replace(chr(39), chr(39) * 2)

new, skipped = [], 0
for e in events:
    if (str(e.get("date", "")), str(e.get("object", ""))) in have:
        skipped += 1
        continue
    if e.get("tag") not in ("ai_observed", "ai_inferred"):
        e["tag"] = "ai_inferred"
    new.append(e)

for e in new:
    sql = ("INSERT INTO learning_events (actor, verb, object_id, object_type, result, confidence, evidence_level, domain, occurred_at, source_tag) VALUES ("
           + chr(39) + "kid1" + chr(39) + ", " + chr(39) + esc(e["verb"]) + chr(39) + ", " + chr(39) + esc(e["object"]) + chr(39) + ", " + chr(39) + "session_record" + chr(39) + ", " + chr(39) + esc(e.get("result", "")) + chr(39) + ", " + str(float(e.get("confidence", 0.7))) + ", " + chr(39) + "L2" + chr(39) + ", " + chr(39) + esc(e.get("domain", "")) + chr(39) + ", " + chr(39) + esc(e["date"]) + chr(39) + ", " + chr(39) + esc(e["tag"]) + chr(39) + ")")
    psql(sql)
print("TOPUP returned=%d inserted=%d dup_skipped=%d" % (len(events), len(new), skipped))
for e in new:
    print("-", e["date"], e["tag"], e["verb"], "|", e["object"])
print("TOTALS:", psql("SELECT source_tag || chr(61) || count(*) FROM learning_events GROUP BY 1").replace("\n", " "))
