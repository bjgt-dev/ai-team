#!/usr/bin/env python3
# intel_pipeline.py - 情报与内容供应链（六步闭环，2026-10-20 董事长令；v3 证据协议对齐版）
import datetime, hashlib, json, os, re, subprocess, time, urllib.request

BASE = "/home/ubuntu/ai-team"
DATE = datetime.date.today().isoformat()
STAGE = f"{BASE}/staging/intel-{DATE}"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) ai-team-intel/1.0"}
YT_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
MODELS = ["foreman-prime", "glm-4-flash-cc"]
SEEN_FILE = f"{BASE}/state/intel-seen.json"
SEEN_TTL_DAYS = 7
# 免费模型/免费渠道关键词（只扫增量新条目，命中即写 outbox 报警）
FREE_KEYWORDS = ["免费模型", "免费额度", "免费API", "免费 API", "免费渠道",
                 "免费算力", "免费 VPS", "免费机子", "免费 token", "免费 Token",
                 "免费订阅", "白嫖", "免费", "羊毛", "额度",
                 "free model", "free API", "free tier",
                 "free credit"]
SKILL_KEYWORDS = ["skill", "skills", "agent-skills", "claude-skills", "SKILL.md"]
SEED_SKILL_REPOS = ["ConardLi/garden-skills", "scenario-labs/skills",
                    "google/skills", "mattpocock/skills",
                    "vercel-labs/agent-skills", "anthropics/skills"]
REGISTRY_FILE = f"{BASE}/state/skills-registry.md"


def get(url, timeout=30, headers=None):
    req = urllib.request.Request(url, headers=headers or UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


# fetch_* return list of (key, text); key is the dedupe key "source:id".
def fetch_github():
    html = get("https://github.com/trending")
    items = []
    for block in html.split("Box-row")[1:]:
        m = re.search(r'href="/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"', block)
        if not m:
            continue
        dm = re.search(r'<p class="[^"]*">\s*(.*?)\s*</p>', block, re.S)
        desc = re.sub(r"<[^>]+>", "", dm.group(1)).strip() if dm else ""
        items.append((f"github:{m.group(1)}", f"{m.group(1)} - {desc[:110]}"))
        if len(items) >= 12:
            break
    return items


def fetch_v2ex():
    data = json.loads(get("https://www.v2ex.com/api/topics/hot.json"))
    out = []
    for t in data[:12]:
        node = t.get("node", {}).get("title", "")
        out.append((f"v2ex:{t.get('id', '')}",
                    f"{t.get('title', '')} (node: {node})"))
    return out


def fetch_bilibili():
    data = json.loads(get("https://api.bilibili.com/x/web-interface/popular?ps=12&pn=1"))
    out = []
    for v in data.get("data", {}).get("list", [])[:12]:
        out.append((f"bilibili:{v.get('bvid', '')}",
                    f"{v.get('title', '')} (zone: {v.get('tname', '')})"))
    return out


def fetch_youtube():
    """No API key: parse ytInitialData from the public search page (verified
    2026-11-06: HTTP 200, 18 videos per query from hub egress)."""
    queries = ["AI agent startup", "free AI API tools",
               "free VPS hosting", "free container platform",
               "free cloud hosting", "免费主机", "免费VPS", "免费容器"]
    out, seen = [], set()
    for q in queries:
        try:
            url = ("https://www.youtube.com/results?search_query="
                   + urllib.request.quote(q))
            html = get(url, timeout=30, headers=YT_UA)
            m = re.search(r"var ytInitialData = (\{.*?\});\s*</script>", html, re.S)
            if not m:
                print(f"youtube query {q!r}: no ytInitialData", flush=True)
                continue
            d = json.loads(m.group(1))
            found = []
            def walk(o):
                if isinstance(o, dict):
                    if "videoRenderer" in o:
                        vr = o["videoRenderer"]
                        runs = vr.get("title", {}).get("runs", [])
                        t = runs[0].get("text", "") if runs else ""
                        ch = (vr.get("longBylineText", {}).get("runs", [{}])[0]
                              .get("text", ""))
                        found.append((vr.get("videoId", ""), t, ch))
                    for v in o.values():
                        walk(v)
                elif isinstance(o, list):
                    for v in o:
                        walk(v)
            walk(d)
            for vid, title, ch in found[:6]:
                if not vid or vid in seen or not title:
                    continue
                seen.add(vid)
                out.append((f"youtube:{vid}",
                            f"{title} (channel: {ch}) | https://youtu.be/{vid}"))
        except Exception as e:
            print(f"youtube query {q!r} failed: {e}", flush=True)
    return out


def _run_helper(name, script, timeout):
    """Run a fetch helper script; stdout is a JSON list of {key, text}."""
    try:
        r = subprocess.run(["python3", f"{BASE}/scripts/{script}"],
                           capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        print(f"source {name} helper failed to run: {e}", flush=True)
        return []
    if r.returncode != 0:
        err = (r.stderr or "").strip().splitlines()
        print(f"source {name} failed: {err[-1] if err else f'exit {r.returncode}'}",
              flush=True)
        return []
    try:
        return [(d["key"], d["text"]) for d in json.loads(r.stdout or "[]")]
    except Exception as e:
        print(f"source {name} bad helper output: {e}", flush=True)
        return []


def fetch_linuxdo():
    """Linux Do 福利羊毛 (category 36) via public JSON + headless Chromium.
    No login: Cloudflare blocks plain curl, Playwright passes. Login-only
    content is out of scope until the login gap is fixed."""
    return _run_helper("Linux Do", "linuxdo_fetch.py", 180)


def fetch_x():
    """X search via logged-in Playwright session. Needs
    state/x-cookies.json (created by a browser task with the Vault login).
    Missing/expired session logs clearly and returns [] (never silent)."""
    return _run_helper("X", "x_fetch.py", 300)


def fetch_skills():
    """GitHub skill repos, no key (60/hr limit): 1 search + up to 5
    SKILL.md checks + 6 seed metadata calls per round."""
    items = []
    gh = {**UA, "Accept": "application/vnd.github+json"}
    try:
        d = json.loads(get("https://api.github.com/search/repositories"
                           "?q=agent-skills+stars:%3E100+in:name,description"
                           "&sort=stars&per_page=10", headers=gh))
        hits = d.get("items", [])[:10]
    except Exception as e:
        print(f"source 技能 search failed: {e}")
        hits = []
    for r in hits[:5]:
        full = r["full_name"]
        confirmed = False
        for path in ("SKILL.md", "skills"):
            try:
                get(f"https://api.github.com/repos/{full}/contents/{path}",
                    timeout=15, headers=gh)
                confirmed = True
                break
            except Exception:
                continue
        mark = "[SKILL.md 已确认]" if confirmed else "[SKILL.md 未确认]"
        items.append((f"skills:{full}",
                      f"{full} ★{r.get('stargazers_count', 0)} - "
                      f"{(r.get('description') or '')[:90]} {mark}"))
    for full in hits[5:]:
        items.append((f"skills:{full['full_name']}",
                      f"{full['full_name']} ★{full.get('stargazers_count', 0)} - "
                      f"{(full.get('description') or '')[:90]} [SKILL.md 未确认]"))
    for full in SEED_SKILL_REPOS:
        try:
            d = json.loads(get(f"https://api.github.com/repos/{full}",
                               timeout=15, headers=gh))
            items.append((f"skills:{full}",
                          f"{full} ★{d.get('stargazers_count', 0)} - "
                          f"{(d.get('description') or '')[:90]} "
                          f"[种子跟踪，推送 {str(d.get('pushed_at', ''))[:10]}]"))
        except Exception as e:
            print(f"source 技能 seed {full} failed: {e}")
    return items


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


def chat(key, prompt, max_tokens=2500):
    last = ""
    for model in MODELS:
        payload = json.dumps({"model": model,
                              "messages": [{"role": "user", "content": prompt}],
                              "max_tokens": max_tokens}).encode()
        for attempt in (1, 2):
            req = urllib.request.Request("http://127.0.0.1:3001/v1/chat/completions", data=payload,
                                         method="POST", headers={"Authorization": "Bearer " + key,
                                                                  "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    d = json.loads(r.read().decode())
                content = (d["choices"][0]["message"]["content"] or "").strip()
                if content:
                    return content, model, ""
                last = f"{model}: empty content"
                break
            except Exception as e:
                last = f"{model}: {type(e).__name__}: {e}"
                if attempt == 1:
                    time.sleep(25)
    return "", "", last


def gemini_filter(raw):
    prompt = ("我们的业务背景：AI 儿童陪伴项目（机器人小蛋糕、真实性铁律、极客奶爸 IP 内容矩阵）、"
              "AI 基建自媒体、目标是内容→信任→小额付费验证。\n"
              "请从中筛选出 3 个最有商业价值/最容易写成爆款的真实痛点，过滤掉广告和废话，并给出理由。\n"
              "输出格式：每个痛点一段，依次写【痛点】【来源】【理由】【落地形式建议】。\n\n"
              "今日原始情报列表：\n" + raw[:3200])
    for attempt in (1, 2):
        out = subprocess.run(["sudo", "python3", "web_brain.py", "--provider", "gemini",
                              "--prompt", prompt, "--timeout", "420"],
                             cwd=f"{BASE}/scripts", capture_output=True, text=True, timeout=600)
        for line in reversed(out.stdout.strip().splitlines()):
            try:
                d = json.loads(line)
            except Exception:
                continue
            ans = d.get("answer") or d.get("text") or d.get("response") or ""
            if d.get("status") == "ok" and len(ans) > 200:
                return ans
    return ""


def sha256(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def gate(task_id, brief_path, draft_path):
    now = datetime.datetime.now().astimezone()
    bundle = f"{BASE}/inbox/{task_id}"
    artifacts = f"{bundle}/artifacts"
    os.makedirs(artifacts, exist_ok=True)
    subprocess.run(["cp", brief_path, f"{artifacts}/brief.md"], check=True)
    subprocess.run(["cp", draft_path, f"{artifacts}/draft.md"], check=True)
    receipts = []
    for rel in ("artifacts/brief.md", "artifacts/draft.md"):
        receipts.append({"tool_name": "write_file", "path": rel, "status": "ok",
                         "output_sha256": sha256(f"{bundle}/{rel}"),
                         "created_at": now.isoformat()})
    with open(f"{bundle}/receipts.jsonl", "w") as f:
        for r in receipts:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    contract = {"task_id": task_id, "task_type": "intel_daily", "domain": "intel",
                "output_file": "artifacts/brief.md",
                "sha256": sha256(f"{artifacts}/brief.md"),
                "criteria": [{"keyword": "今日商机", "min_chars": 500}],
                "created_at": (now - datetime.timedelta(seconds=120)).isoformat(),
                "acceptance_criteria": [
                    {"type": "file_exists", "target": "artifacts/brief.md"},
                    {"type": "file_exists", "target": "artifacts/draft.md"},
                    {"type": "file_min_size", "target": "artifacts/brief.md", "expected": 500}]}
    cpath = f"{BASE}/contracts/pending/muse/{task_id}.json"
    os.makedirs(os.path.dirname(cpath), exist_ok=True)
    with open(cpath, "w") as f:
        json.dump(contract, f, ensure_ascii=False, indent=1)
    subprocess.run(["python3", f"{BASE}/muse-worker/gen_evidence.py", task_id, bundle, cpath],
                   capture_output=True, timeout=60)
    # 2026-10-21 fix (closure item 7): do not rely on a scan_inbox.sh full
    # pass. scan_inbox re-processes the whole inbox backlog serially, so the
    # old 120s cap + 180s poll lost the race on 2026-10-21 (verdict NO_VERDICT
    # although the bundle PASSed minutes later). Stage the canonical contract
    # copy exactly like scan_inbox.sh does, then invoke ingest.sh for THIS
    # task only (ingest.sh itself is frozen; it is invoked, never modified).
    # Publish requires verify PASS *and* review PASS.
    t0 = time.time()
    canonical = f"{BASE}/contracts/{task_id}.json"
    # 2026-11-06 fix: always overwrite the canonical contract. A second run
    # on the same day reuses task_id; the old guard (copy only if absent)
    # left the morning run's contract in place, so verify compared the old
    # sha against the new brief and REJECTed with "sha256 mismatch".
    subprocess.run(["cp", "-p", cpath, canonical], check=True)
    try:
        subprocess.run(["bash", f"{BASE}/scripts/ingest.sh", task_id],
                       capture_output=True, timeout=600)
    except Exception as e:
        print(f"direct ingest failed ({e}); falling back to verdict poll")
    vlog = f"{BASE}/logs/verify-{task_id}.json"
    rlog = f"{BASE}/logs/review-{task_id}.json"
    for _ in range(150):
        if os.path.exists(vlog) and os.path.getmtime(vlog) >= t0 - 5:
            verdict = json.load(open(vlog)).get("verdict", "UNKNOWN")
            if verdict != "PASS":
                return verdict
            if os.path.exists(rlog) and os.path.getmtime(rlog) >= t0 - 5:
                rverdict = json.load(open(rlog)).get("verdict", "FAIL")
                return "PASS" if rverdict == "PASS" else "REVIEW_FAIL"
        time.sleep(2)
    return "NO_VERDICT"


def load_seen():
    """Dedupe state: {key: first_seen_iso}. Pruned to SEEN_TTL_DAYS."""
    try:
        d = json.load(open(SEEN_FILE))
        seen = d.get("seen", {}) if isinstance(d, dict) else {}
    except Exception:
        seen = {}
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(days=SEEN_TTL_DAYS))
    fresh = {}
    for k, v in seen.items():
        try:
            ts = datetime.datetime.fromisoformat(v)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=datetime.timezone.utc)
            if ts >= cutoff:
                fresh[k] = v
        except Exception:
            continue
    return fresh


def save_seen(seen):
    tmp = SEEN_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"seen": seen, "updated": datetime.datetime.now(
            datetime.timezone.utc).isoformat()}, f, ensure_ascii=False)
    os.replace(tmp, SEEN_FILE)


def collect():
    """相位 1：采集。返回 (sources, raw, new_items)。
    sources: {name: count}; raw: markdown sections for the brief;
    new_items: [(source, key, text)] not seen before this run.
    Every fetched key is marked seen, so the morning run and the 3-hourly
    incremental rounds never re-report each other's items."""
    seen = load_seen()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    sources, sections, new_items = {}, [], []
    jobs = [("GitHub Trending", fetch_github), ("V2EX", fetch_v2ex),
            ("Bilibili", fetch_bilibili), ("YouTube", fetch_youtube),
            ("Linux Do", fetch_linuxdo), ("X", fetch_x),
            ("技能", fetch_skills)]
    for name, fn in jobs:
        try:
            items = fn()
            sources[name] = len(items)
        except Exception as e:
            items, sources[name] = [], 0
            print(f"source {name} failed: {e}")
        for key, text in items:
            if key not in seen:
                new_items.append((name, key, text))
            seen[key] = seen.get(key, now_iso)
        if items:
            sections.append(f"## {name}\n" + "\n".join(f"- {t}" for _, t in items))
    save_seen(seen)
    return sources, "\n\n".join(sections), new_items


FREE_WORD_RE = re.compile(r"\bfree\b", re.IGNORECASE)


def scan_free_goods(new_items):
    """Return new items matching free-model / free-channel keywords.

    2026-11-08 fix: bare FREE missed (e.g. 'Get 7 BILLION AI Tokens FREE
    Every Month' matched none of the multi-word phrases). Word-boundary
    match on the standalone word catches it without matching 'freedom'.
    """
    hits = []
    for name, key, text in new_items:
        low = text.lower()
        if any(k.lower() in low for k in FREE_KEYWORDS) or FREE_WORD_RE.search(text):
            hits.append((name, key, text))
    return hits


def scan_skills(new_items):
    """Append confirmed new skill repos to the registry auto-append section."""
    confirmed = [(n, k, t) for n, k, t in new_items
                 if n == "技能" and "[SKILL.md 已确认]" in t]
    if not confirmed:
        return 0
    os.makedirs(os.path.dirname(REGISTRY_FILE), exist_ok=True)
    section = "## 七、待验技能线索（增量扫自动追加）"
    existing = ""
    if os.path.exists(REGISTRY_FILE):
        with open(REGISTRY_FILE, encoding="utf-8") as f:
            existing = f.read()
    lines = [f"- {t} · {DATE}" for _, _, t in confirmed]
    with open(REGISTRY_FILE, "a", encoding="utf-8") as f:
        if section not in existing:
            f.write("\n" + section + "\n")
        f.write("\n".join(lines) + "\n")
    return len(confirmed)


def run_incremental():
    """3-hourly incremental round: only content new since the last round.
    Appends new items to the daily incremental record; a free-model /
    free-channel hit also writes an outbox alert immediately."""
    now = datetime.datetime.now().astimezone()
    sources, raw, new_items = collect()
    with open(f"{STAGE}/raw_list.md", "w") as f:
        f.write(raw)
    skill_items = [(n, k, t) for n, k, t in new_items if n == "技能"]
    result = {"mode": "incremental", "time": now.isoformat(),
              "sources": sources, "new": len(new_items),
              "skill_new": len(skill_items)}
    if not new_items:
        print(json.dumps(result, ensure_ascii=False))
        return result
    rec_path = f"{BASE}/state/intel-incremental-{DATE}.md"
    os.makedirs(os.path.dirname(rec_path), exist_ok=True)
    block = (f"\n## {now.strftime('%H:%M')} 增量（{len(new_items)} 条新内容）\n"
             + "\n".join(f"- [{n}] {t}" for n, _, t in new_items) + "\n")
    if skill_items:
        block = (f"\n## 技能（{len(skill_items)} 条）\n"
                 + "\n".join(f"- {t}" for _, _, t in skill_items) + "\n") + block
    with open(rec_path, "a") as f:
        f.write(block)
    result["record"] = rec_path
    hits = scan_free_goods(new_items)
    result["free_hits"] = len(hits)
    result["skills_appended"] = scan_skills(new_items)
    if hits:
        alert_path = (f"{BASE}/state/outbox/intel-new-free-{DATE}-"
                      f"{now.strftime('%H%M')}.md")
        with open(alert_path, "w") as f:
            f.write(f"# 情报增量：新发现免费模型/渠道 · {now.strftime('%Y-%m-%d %H:%M')}\n\n"
                    + "\n".join(f"- [{n}] {t}" for n, _, t in hits) + "\n")
        result["alert"] = alert_path
    print(json.dumps(result, ensure_ascii=False))
    return result


def load_cached_raw():
    """相位 1 的缓存读取（过载时只入库不采集）。"""
    p = f"{STAGE}/raw_list.md"
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return f.read()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-collect", action="store_true", help="不采集，仅用当日缓存 raw_list.md 进入入库相位")
    ap.add_argument("--collect-only", action="store_true", help="只跑采集相位，随后退出（用于双路实测）")
    ap.add_argument("--incremental", action="store_true", help="3 小时增量轮：只取上轮之后的新内容，去重后写入当日增量记录；免费模型/渠道命中写 outbox 报警")
    ap.add_argument("--dry-run", action="store_true", help="不调外脑/不建契约，只验证相位流转")
    ap.add_argument("--overload-pct", type=int, default=None, help="触发跳过采集的实测负载百分比（仅日志）")
    args = ap.parse_args()
    os.makedirs(STAGE, exist_ok=True)
    if args.incremental:
        if args.skip_collect:
            print(json.dumps({"mode": "incremental", "result": "SKIPPED_OVERLOAD",
                              "overload_pct": args.overload_pct}, ensure_ascii=False))
            return
        run_incremental()
        return
    if args.skip_collect:
        raw = load_cached_raw()
        if raw is None:
            print(json.dumps({"mode": "ingest-only", "overload_pct": args.overload_pct,
                              "result": "SKIPPED_NO_CACHE"}, ensure_ascii=False))
            return
        sources = {"cached": 1}
        print(json.dumps({"mode": "ingest-only", "overload_pct": args.overload_pct,
                          "result": "COLLECT_SKIPPED_USING_CACHE", "cache_bytes": len(raw)}, ensure_ascii=False))
    else:
        sources, raw, _new = collect()
        with open(f"{STAGE}/raw_list.md", "w") as f:
            f.write(raw)
        print(json.dumps({"mode": "full-collect", "sources": sources, "raw_bytes": len(raw)}, ensure_ascii=False))
        if args.collect_only:
            return
    if args.dry_run:
        print(json.dumps({"mode": "dry-run", "raw_bytes": len(raw), "phases": ["collect" if not args.skip_collect else "cached", "generate", "gate", "store"]}, ensure_ascii=False))
        return
    picks = gemini_filter(raw)
    gemini_ok = bool(picks)
    if not gemini_ok:
        picks = "（外脑今晚未通，以下为未筛选原始榜单摘录，简报降级生成）\n" + raw[:2000]
    brief = (f"# 今日商机与爆款简报 · {DATE}\n\n来源覆盖："
             + "、".join(f"{k} {v} 条" for k, v in sources.items())
             + f"\n外脑筛选：{'Gemini 已筛选' if gemini_ok else '未通（降级）'}\n\n"
             f"## 外脑精选 3 个痛点\n\n{picks}\n\n## 原始榜单（备查）\n\n{raw}\n")
    brief_path = f"{STAGE}/brief.md"
    with open(brief_path, "w") as f:
        f.write(brief)
    key = read_key()
    draft, dmodel, derr = ("", "", "no key") if not key else chat(
        key,
        "你是极客奶爸 IP 的小红书撰稿。下面是今天外脑筛选出的 3 个痛点：\n" + picks[:2200] +
        "\n\n请挑其中与「育儿/极客/AI 陪伴」最贴合的一个，写一篇约 500 字的小红书图文草稿。"
        "要求：父亲第一人称口语、短句、真实感；只许用痛点里已有的事实，不许编造具体事件和数字；"
        "结尾留一个互动提问；给 3-5 个话题标签。输出：第一行是标题，其后正文。")
    draft_path = f"{STAGE}/draft.md"
    with open(draft_path, "w") as f:
        f.write((draft or f"(draft failed: {derr})") + "\n")
    task_id = f"intel-daily-{DATE.replace('-', '')}"
    verdict = gate(task_id, brief_path, draft_path)
    result = {"date": DATE, "sources": sources, "gemini_ok": gemini_ok,
              "draft_ok": bool(draft), "draft_model": dmodel, "verdict": verdict}
    if verdict == "PASS":
        os.makedirs(f"{BASE}/knowledge/intel", exist_ok=True)
        os.makedirs(f"{BASE}/knowledge/ip-content", exist_ok=True)
        subprocess.run(["cp", brief_path, f"{BASE}/knowledge/intel/brief-{DATE}.md"])
        subprocess.run(["cp", draft_path, f"{BASE}/knowledge/ip-content/draft-{DATE}.md"])
        with open(f"{BASE}/state/intel_latest.md", "w") as f:
            f.write(brief + "\n---\n# 小红书草稿（复制即发）\n\n" + (draft or "（无）") + "\n")
        result["stored"] = True
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
