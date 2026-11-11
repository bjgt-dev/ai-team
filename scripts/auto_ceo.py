#!/usr/bin/env python3
# auto_ceo.py - 自动巡航 CEO（tmux 常驻，2026-10-20 董事长令）
# 每 2 小时醒来一次：读 state -> 调 newapi 免费池推演 -> 写巡航日志 -> 高价值才推送
# 定位：只"想+报"，不直接执行重活（执行走 OpenClaw 契约流水线，列车长/发动机边界不破）
# 推送：Server酱（state/.pushkey 存在才启用）；无 key 时只落盘，由主会话 cron 体系转投
import datetime, json, os, subprocess, time, urllib.request, urllib.parse

BASE = "/home/ubuntu/ai-team"
STATE = f"{BASE}/state"
CRUISE_DIR = f"{STATE}/ceo_cruise"
CYCLE_SECONDS = 2 * 3600
MODELS = ["foreman-prime", "glm-4-flash-cc"]


def now():
    return datetime.datetime.now().astimezone()


def read_key():
    cmd = ('mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e '
           '"SELECT tokens.key FROM oneapi.tokens WHERE id=12" 2>/dev/null')
    out = subprocess.run(["docker", "exec", "ai-stack-new-api-db-1", "sh", "-c", cmd],
                         capture_output=True, text=True)
    for line in out.stdout.splitlines():
        tok = line.strip().split()[-1] if line.strip() else ""
        if len(tok) >= 20:
            return tok if tok.startswith("sk-") else "sk-" + tok
    return ""


def read_file(path, limit=3000):
    try:
        return open(path).read()[:limit]
    except Exception:
        return ""


def think(context):
    key = read_key()
    if not key:
        return None, "no key"
    prompt = ("你是 AI 事业部 CEO 的代班巡航脑。基于以下系统状态，做一次巡航推演。\n"
              "北极星：赚钱（内容→信任→小额付费验证）、IP 影响力、系统 24h 自转。\n"
              "只输出 JSON：{\"next_action\": \"下一步最值得做的 1 件事\", \"why\": \"理由≤60字\", "
              "\"high_value\": true/false（是否值得立刻推送老板：重大商机/严重故障/关键决策点才 true）, "
              "\"note\": \"给老板的一句话（≤50字），无则空字符串\"}\n\n系统状态：\n" + context)
    last = ""
    for model in MODELS:
        payload = json.dumps({"model": model,
                              "messages": [{"role": "user", "content": prompt}],
                              "max_tokens": 2000}).encode()
        req = urllib.request.Request("http://127.0.0.1:3001/v1/chat/completions", data=payload,
                                     method="POST", headers={"Authorization": "Bearer " + key,
                                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.loads(r.read().decode())
            text = d["choices"][0]["message"]["content"] or ""
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start:end + 1]), ""
            last = f"{model}: no JSON in output"
        except Exception as e:
            last = f"{model}: {type(e).__name__}: {e}"
            time.sleep(10)
    return None, last


def push(title, body):
    keyfile = f"{STATE}/.pushkey"
    if not os.path.exists(keyfile):
        return "skipped (no pushkey)"
    sendkey = open(keyfile).read().strip()
    if not sendkey:
        return "skipped (empty pushkey)"
    data = urllib.parse.urlencode({"title": title, "desp": body}).encode()
    req = urllib.request.Request(f"https://sctapi.ftqq.com/{sendkey}.send", data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode())
        return f"serverchan code={d.get('code')}"
    except Exception as e:
        return f"push failed: {type(e).__name__}"


def cycle():
    os.makedirs(CRUISE_DIR, exist_ok=True)
    alerts_marker = f"{CRUISE_DIR}/.alerts_seen"
    alerts_now = read_file(f"{STATE}/alerts.log", 100000)
    seen = int(open(alerts_marker).read()) if os.path.exists(alerts_marker) else len(alerts_now)
    new_alerts = alerts_now[seen:] if len(alerts_now) >= seen else alerts_now
    context = ("## pending\n" + read_file(f"{STATE}/pending.md") +
               "\n## handoff\n" + read_file(f"{STATE}/handoff.md", 1500) +
               "\n## strategy pack\n" + read_file(f"{STATE}/strategy_latest.md", 1500) +
               "\n## new alerts\n" + (new_alerts.strip() or "无"))
    verdict, err = think(context)
    stamp = now().strftime("%F %T")
    entry = {"time": stamp, "verdict": verdict, "error": err,
             "new_alerts": bool(new_alerts.strip())}
    with open(f"{CRUISE_DIR}/cruise-{now():%Y%m%d}.jsonl", "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    with open(alerts_marker, "w") as f:
        f.write(str(len(alerts_now)))
    # 推送条件：模型判定高价值，或出现新告警。每轮最多推 1 条，防噪音。
    if new_alerts.strip():
        entry["push"] = push("CEO 巡航告警", new_alerts.strip()[:400])
    elif verdict and verdict.get("high_value"):
        entry["push"] = push("CEO 巡航简报", f"{verdict.get('next_action','')}\n{verdict.get('note','')}")
    else:
        entry["push"] = "not needed"
    print(f"[{stamp}] cycle done | action={ (verdict or {}).get('next_action','-') } | push={entry['push']} | err={err}")
    return entry


def main():
    once = "--once" in os.sys.argv
    print(f"auto_ceo started (once={once}) pid={os.getpid()}")
    while True:
        try:
            cycle()
        except Exception as e:
            print(f"cycle crashed: {type(e).__name__}: {e}")
        if once:
            break
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    main()
