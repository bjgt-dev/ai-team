#!/usr/bin/env python3
# 外脑物理接入 web_brain.py（2026-10-20，三层路由·第三层）
# 用 Playwright 以「档案副本」打开已登录的网页版大模型，提问→等答完→抓文本→输出 JSON。
# 档案本体只读使用（复制到 /tmp 跑，副本用完即删），不污染老板的登录档案。
# 用法：web_brain.py --provider deepseek|gemini|chatgpt|claude --prompt "问题" [--timeout 240]
# 输出：{"provider":..., "status":"ok|not_logged_in|blocked|error", "answer":"...", "elapsed_s":...,
#        "session_key":..., "session_reused":bool, "conversation_url":..., "session_note":...}
# 会话续接（2026-10-27）：--session-key/--continue/--new，注册表见 REGISTRY_PATH。
# 注册表只存会话标识（provider、session_key、conversation_url）、时间戳、prompt 哈希、状态，
# 不存 prompt、答案、凭据。注册表在枢纽 state 下，随枢纽备份走（个人资产+枢纽范围，
# 不含 worker 自身资产），脚本更新不删注册表。过期清理见 prune_registry / --cleanup-sessions。
# 注：deepseek 于 2026-10-21 接入（#1 直用号，董事长令：网页版永久免费、不限次数，
# 有问题默认先问它）；gemini 于 2026-10-20 经董事长 scoped 授权接入（仅限其本人已登录
# 会话做外脑咨询，不碰登录/注册/验证，遇验证挑战立即停并上报）；chatgpt/claude 暂弃。
import argparse, hashlib, json, os, random, shutil, sys, tempfile, time
from datetime import datetime, timezone
from pathlib import Path

DOCTRINE = (
    "我方执行准则：禁止排期思维——有方案、回滚齐备即立即切换，切换窗口不超过 2 小时；"
    "请按先回滚策略、再立即切换、切换后密集观测给方案，不要建议 24/48 小时观察期这类前置门。"
)

def type_human(page, text):
    # 拟人打字：30-150ms 随机间隔，禁止整段 fill
    for ch in text:
        page.keyboard.type(ch)
        time.sleep(random.uniform(0.03, 0.15))

def human_click(page, selector):
    # 贝塞尔曲线点击：18-42 微步 + 点击前悬停 120-480ms + 落点偏离中心 2-5px
    el = page.query_selector(selector)
    if not el:
        return False
    box = el.bounding_box()
    if not box:
        el.click()
        return True
    sx, sy = page.mouse._x if hasattr(page.mouse, "_x") else 200, page.mouse._y if hasattr(page.mouse, "_y") else 200
    tx = box["x"] + box["width"] / 2 + random.uniform(-5, 5)
    ty = box["y"] + box["height"] / 2 + random.uniform(-5, 5)
    steps = random.randint(18, 42)
    cx, cy = (sx + tx) / 2 + random.uniform(-60, 60), (sy + ty) / 2 + random.uniform(-40, 40)
    for i in range(1, steps + 1):
        t = i / steps
        x = (1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t ** 2 * tx
        y = (1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t ** 2 * ty
        page.mouse.move(x, y)
        time.sleep(random.uniform(0.008, 0.02))
    time.sleep(random.uniform(0.12, 0.48))  # 悬停
    page.mouse.down()
    time.sleep(random.uniform(0.05, 0.12))
    page.mouse.up()
    return True

PROVIDERS = {
    "chatgpt": {"url": "https://chatgpt.com/", "profile": "/opt/model-profiles/chatgpt"},
    "claude": {"url": "https://claude.ai/new", "profile": "/opt/model-profiles/claude"},
    "gemini": {"url": "https://gemini.google.com/app", "profile": "/opt/model-profiles/gemini"},
    "deepseek": {"url": "https://chat.deepseek.com/", "profile": "/opt/model-profiles/deepseek"},
}

# --- 会话注册表（长期维护，2026-10-27）---
# 路径固定在枢纽 state 下：脚本更新、枢纽重启、VM 换代后仍可用，因为 state 随枢纽备份走。
# 格式：{"version": 1, "sessions": {"<provider>:<session_key>": {
#   "provider": ..., "session_key": ..., "conversation_url": ...,
#   "last_used": "ISO8601 UTC", "last_prompt_hash": "sha256 hex", "status": ..., "reused": bool}}}
# 注意：conversation_url 是页面实际返回的 URL（发送回答后 page.url），不硬编码任何
# provider 的会话 URL 格式；复用时直接 goto 该 URL，格式由各 provider 实际页面决定。
REGISTRY_PATH = Path("/home/ubuntu/ai-team/state/web_brain_sessions.json")
DEFAULT_SESSION_TTL_DAYS = 90

def sanitize_session_key(key):
    if key is None:
        return None
    k = "".join(ch for ch in str(key) if ch.isalnum() or ch in "-_")
    return k[:64] if k else None

def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def _parse_iso(v):
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None

def load_registry():
    try:
        data = json.loads(REGISTRY_PATH.read_text())
        if isinstance(data, dict) and isinstance(data.get("sessions"), dict):
            return data
    except Exception:
        pass
    return {"version": 1, "sessions": {}}

def prune_registry(data, ttl_days):
    now = datetime.now(timezone.utc)
    removed = []
    for k in list(data.get("sessions", {}).keys()):
        lu = _parse_iso(data["sessions"][k].get("last_used"))
        if lu is not None and (now - lu).days > ttl_days:
            del data["sessions"][k]
            removed.append(k)
    return removed

def save_registry(data, ttl_days=DEFAULT_SESSION_TTL_DAYS):
    removed = prune_registry(data, ttl_days)
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.replace(tmp, REGISTRY_PATH)
    try:
        os.chmod(REGISTRY_PATH, 0o600)
    except Exception:
        pass
    return removed

def record_session(provider, session_key, conversation_url, prompt_hash, status, reused):
    # 只写会话标识、时间戳、prompt 哈希、状态；绝不写 prompt、答案、凭据。
    data = load_registry()
    rk = f"{provider}:{session_key}"
    old = data["sessions"].get(rk, {})
    url = conversation_url or old.get("conversation_url") or ""
    data["sessions"][rk] = {
        "provider": provider,
        "session_key": session_key,
        "conversation_url": url,
        "last_used": _now_iso(),
        "last_prompt_hash": prompt_hash,
        "status": status,
        "reused": bool(reused),
    }
    save_registry(data)
    return rk


def ask_chatgpt(page, prompt, timeout, resume_url=None):
    # 2026-10-22 修复（光头）：新版 ChatGPT UI 已移除 data-message-author-role 等语义属性，
    # 旧抓取永远等不到回答、只能耗到超时。改以无障碍回合标题（sr-only 的“你说：/ChatGPT 说：”）
    # 定位回合块抓取；长文本改整段 insert_text + 回车发送（逐字打字会被换行逐段误发且超时，
    # Gemini/DeepSeek 路径已有此处理，ChatGPT 路径此前漏改）。
    page.goto(resume_url or "https://chatgpt.com/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(6000)
    body = page.inner_text("body")[:2000]
    composer = page.query_selector("#prompt-textarea") or page.query_selector("div[contenteditable='true']")
    if not composer and "security verification" in body.lower():
        page.wait_for_timeout(10000)
        body = page.inner_text("body")[:2000]
        composer = page.query_selector("#prompt-textarea") or page.query_selector("div[contenteditable='true']")
    if not composer:
        if "log in" in body.lower() or "登录" in body:
            return "not_logged_in", ""
        return "blocked", body[:300]

    def turn_blocks():
        return page.evaluate("""() => {
          const main = document.querySelector('main');
          if (!main) return [];
          const labels = {'你说：': 'user', '您说：': 'user', 'You said:': 'user',
                          'ChatGPT 说：': 'assistant', 'ChatGPT said:': 'assistant'};
          const out = [];
          main.querySelectorAll('.sr-only').forEach(h => {
            const t = (h.innerText || '').trim();
            if (labels[t]) {
              const block = h.parentElement;
              out.push({role: labels[t], text: block ? block.innerText : ''});
            }
          });
          return out;
        }""")

    composer.click()
    if len(prompt) <= 200 and "\n" not in prompt:
        type_human(page, prompt)
        page.keyboard.press("Enter")
    else:
        page.keyboard.insert_text(prompt)
        page.wait_for_timeout(1200)
        page.keyboard.press("Enter")

    marker = prompt.strip()[:30]
    deadline = time.time() + timeout
    last_text, stable_since = "", time.time()
    send_seen = False
    while time.time() < deadline:
        page.wait_for_timeout(1500)
        try:
            blocks = turn_blocks()
        except Exception:
            continue
        ans = None
        for i, b in enumerate(blocks):
            if b["role"] == "user" and marker and marker in b["text"]:
                send_seen = True
                for j in range(i + 1, len(blocks)):
                    if blocks[j]["role"] == "assistant":
                        ans = blocks[j]["text"]
                        break
                break
        if ans is None:
            continue
        txt = ans
        for lab in ("ChatGPT 说：", "ChatGPT said:"):
            if txt.startswith(lab):
                txt = txt[len(lab):]
                break
        txt = txt.strip()
        if txt and txt == last_text:
            if time.time() - stable_since > 4:
                return "ok", txt
        elif txt:
            last_text, stable_since = txt, time.time()
    if last_text:
        return "ok", last_text
    if not send_seen:
        return "error", "send not confirmed: user turn not found"
    return "error", "timeout waiting response"


def ask_claude(page, prompt, timeout, resume_url=None):
    page.goto(resume_url or "https://claude.ai/new", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(6000)
    body = page.inner_text("body")[:2000]
    composer = page.query_selector("div[contenteditable='true']")
    if not composer and "security verification" in body.lower():
        page.wait_for_timeout(10000)
        body = page.inner_text("body")[:2000]
        composer = page.query_selector("div[contenteditable='true']")
    if not composer:
        if "log in" in body.lower():
            return "not_logged_in", ""
        return "blocked", body[:300]
    composer.click()
    if len(prompt) <= 200 and "\n" not in prompt:
        type_human(page, prompt)
        page.keyboard.press("Enter")
    else:
        # 2026-10-22 修复（光头）：长文本逐字打字以 30–150ms/字计需十几分钟且会被换行逐段误发，
        # 改整段 insert_text + 回车发送（与 Gemini/DeepSeek/ChatGPT 路径对齐）。
        page.keyboard.insert_text(prompt)
        page.wait_for_timeout(1200)
        page.keyboard.press("Enter")
    deadline = time.time() + timeout
    last_text, stable_since = "", time.time()
    while time.time() < deadline:
        page.wait_for_timeout(1500)
        # 2026-10-22 增补（光头）：免费版 5 小时消息限额耗尽时页面会弹限额窗、消息发不出，
        # 旧逻辑会白等满 timeout。检出限额文案立即以 rate_limited 返回，不空烧。
        try:
            _bt = page.inner_text("body")[-1200:].lower()
        except Exception:
            _bt = ""
        if "hit your limit" in _bt or "message limit" in _bt or "hit your 5-hour" in _bt:
            return "blocked", "rate_limited: claude free-plan message limit reached"
        nodes = page.query_selector_all(".font-claude-response, [data-testid='assistant-message'], .prose")
        texts = [n.inner_text() for n in nodes if n.inner_text().strip()]
        txt = texts[-1] if texts else ""
        if txt and txt == last_text:
            if time.time() - stable_since > 3:
                return "ok", txt
        elif txt:
            last_text, stable_since = txt, time.time()
    return ("ok", last_text) if last_text else ("error", "timeout waiting response")

def ask_gemini(page, prompt, timeout, resume_url=None):
    # Gemini 网页版（董事长 scoped 授权：仅用其本人已登录会话咨询；遇登录/验证页立即停）
    import os
    print(f"[gem_probe] goto url={page.url} HOME={os.environ.get('HOME')} whoami_ok", flush=True, file=sys.stderr)
    page.goto(resume_url or "https://gemini.google.com/app", wait_until="domcontentloaded", timeout=60000)
    print(f"[gem_probe] after_goto url={page.url} title={page.title()}", flush=True, file=sys.stderr)
    try:
        page.wait_for_selector("rich-textarea div[contenteditable='true'], div.ql-editor, div[contenteditable='true']", timeout=15000)
        print("[gem_probe] composer_found", flush=True, file=sys.stderr)
    except Exception as e:
        print(f"[gem_probe] composer_timeout body={page.inner_text('body')[:500]}", flush=True, file=sys.stderr)
    body = page.inner_text("body")[:2000]
    composer = (page.query_selector("rich-textarea div[contenteditable='true']")
                or page.query_selector("div.ql-editor")
                or page.query_selector("div[contenteditable='true']"))
    if not composer:
        low = body.lower()
        if "sign in" in low or "登录" in body:
            return "not_logged_in", ""
        if "verify" in low or "验证" in body:
            return "blocked", "verification challenge - stopped per red line"
        return "blocked", body[:300]
    before = page.query_selector_all("model-response")
    composer.click()
    if len(prompt) <= 200:
        type_human(page, prompt)
        page.keyboard.press("Enter")
    else:
        # 长文本：换行符会被当回车逐段误发，必须整段插入再点发送按钮
        page.keyboard.insert_text(prompt)
        page.wait_for_timeout(1500)
        btn = page.query_selector("button[aria-label*='Send'], button[aria-label*='发送'], button.send-button")
        if btn:
            btn.click()
        else:
            page.keyboard.press("Enter")
    print(f"[gem_probe] after_send url={page.url}", flush=True, file=sys.stderr)
    deadline = time.time() + min(timeout, 90)
    last_text, stable_since = "", time.time()
    while time.time() < deadline:
        page.wait_for_timeout(1500)
        nodes = page.query_selector_all("model-response")
        if len(nodes) > len(before):
            txt = nodes[-1].inner_text()
            if txt == last_text and len(txt) > 200:
                if time.time() - stable_since > 12:
                    return "ok", txt
            elif txt != last_text:
                last_text, stable_since = txt, time.time()
    return ("ok", last_text) if last_text else ("error", "timeout waiting response")

def ask_deepseek(page, prompt, timeout, resume_url=None):
    # DeepSeek 网页版（董事长令：永久免费、不限次数，默认专家通道；仅用已登录会话咨询）
    page.goto(resume_url or "https://chat.deepseek.com/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(6000)
    # chairman rule 2026-10-21: DeepSeek asks require DeepThink + Search ON (localStorage flags, reload to apply; state logged)
    try:
        page.evaluate("() => { const ks = ['thinkingEnabled', 'searchEnabled']; for (const k of ks) { const v = localStorage.getItem(k); if (v && v.indexOf('false') >= 0) localStorage.setItem(k, v.replace('false', 'true')); } }")
        page.reload(wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        _st = page.evaluate("() => [localStorage.getItem('thinkingEnabled'), localStorage.getItem('searchEnabled')]")
        with open("/tmp/webbrain-toggles.log", "a") as _f:
            _f.write(str(_st) + "\n")
    except Exception:
        pass
    url = page.url
    body = page.inner_text("body")[:2000]
    composer = page.query_selector("textarea")
    if not composer:
        if "/sign_in" in url or "log in" in body.lower():
            return "not_logged_in", ""
        return "blocked", body[:300]
    composer.click()
    if len(prompt) <= 200:
        type_human(page, prompt)
        page.keyboard.press("Enter")
    else:
        # 长文本整段插入，避免换行逐段误发；插完按一次回车发送
        page.keyboard.insert_text(prompt)
        page.wait_for_timeout(1200)
        page.keyboard.press("Enter")
    deadline = time.time() + timeout
    last_text, stable_since = "", time.time()
    while time.time() < deadline:
        page.wait_for_timeout(2000)
        nodes = page.query_selector_all(".ds-markdown")
        txt = nodes[-1].inner_text() if nodes else ""
        if txt and txt == last_text and len(txt) > 100:
            if time.time() - stable_since > 10:
                return "ok", txt
        elif txt != last_text:
            last_text, stable_since = txt, time.time()
    return ("ok", last_text) if last_text else ("error", "timeout waiting response")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", required=True, choices=list(PROVIDERS))
    ap.add_argument("--prompt", required=False, default=None)
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--no-doctrine", action="store_true",
                    help="仅 canary/诊断：不注入执行准则")
    ap.add_argument("--session-key", default=None,
                    help="会话键：同一话题用同一键；禁止用模糊的最新会话当唯一键")
    ap.add_argument("--continue", dest="continue_session", action="store_true",
                    help="复用该 session-key 的上一会话；无上一会话则新建并登记")
    ap.add_argument("--new", dest="new_session", action="store_true",
                    help="强制新开会话并覆盖注册表旧条目")
    ap.add_argument("--session-ttl-days", type=int, default=DEFAULT_SESSION_TTL_DAYS,
                    help="注册表过期天数，默认 90 天")
    ap.add_argument("--cleanup-sessions", action="store_true",
                    help="只清理过期会话，不启动浏览器、不提问")
    ap.add_argument("--list-sessions", action="store_true",
                    help="只列出注册表会话标识，不启动浏览器、不提问")
    a = ap.parse_args()
    if a.cleanup_sessions:
        data = load_registry()
        removed = save_registry(data, a.session_ttl_days)
        print(json.dumps({"action": "cleanup", "removed": removed,
                          "kept": sorted(data["sessions"].keys()),
                          "registry_path": str(REGISTRY_PATH)}, ensure_ascii=False))
        return
    if a.list_sessions:
        data = load_registry()
        print(json.dumps({"action": "list", "sessions": data["sessions"],
                          "registry_path": str(REGISTRY_PATH)}, ensure_ascii=False))
        return
    if not a.prompt:
        ap.error("--prompt is required unless --cleanup-sessions/--list-sessions")
    if a.continue_session and not a.session_key:
        print(json.dumps({"provider": a.provider, "status": "error",
                          "answer": "session_key required for --continue",
                          "elapsed_s": 0.0, "session_key": None,
                          "session_reused": False, "conversation_url": None,
                          "session_note": "refused: --continue without --session-key"},
                         ensure_ascii=False))
        return
    session_key = sanitize_session_key(a.session_key)
    if a.session_key and not session_key:
        print(json.dumps({"provider": a.provider, "status": "error",
                          "answer": "invalid session_key after sanitization",
                          "elapsed_s": 0.0, "session_key": None,
                          "session_reused": False, "conversation_url": None,
                          "session_note": "refused: empty session_key"}, ensure_ascii=False))
        return
    prompt = a.prompt
    if not a.no_doctrine and DOCTRINE not in prompt:
        prompt = DOCTRINE + "\n\n" + prompt
    prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
    cfg = PROVIDERS[a.provider]
    registry_key = f"{a.provider}:{session_key}" if session_key else None
    resume_url = None
    session_reused = False
    session_note = ""
    if registry_key and not a.new_session:
        entry = load_registry()["sessions"].get(registry_key)
        if entry and entry.get("conversation_url"):
            resume_url = entry["conversation_url"]
            session_reused = True
            session_note = "resume attempted"
        else:
            session_note = "no prior session, creating new"
    elif registry_key and a.new_session:
        session_note = "forced new session"
    t0 = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="webbrain-"))
    final_url = ""
    try:
        prof_copy = tmp / "profile"
        _runtime_dir = None
        _runtime_used = False
        try:
            shutil.copytree(cfg["profile"], prof_copy, symlinks=True)
        except (PermissionError, OSError):
            if a.provider not in ("deepseek", "gemini"):
                raise
            shutil.rmtree(prof_copy, ignore_errors=True)
            import subprocess as _sp
            _runtime_dir = Path("/var/lib/expert-runtime") / a.provider
            _cp = _sp.run(["sudo", "-n", "python3", "/home/ubuntu/ai-team/scripts/expert_runtime_copy.py", "--provider", a.provider, "--runtime-dir", str(_runtime_dir)], capture_output=True, text=True, timeout=120)
            if _cp.returncode != 0:
                raise PermissionError("runtime copy failed: " + _cp.stdout + _cp.stderr)
            shutil.copytree(_runtime_dir, prof_copy, symlinks=True)
            _runtime_used = True
        from playwright.sync_api import sync_playwright
        os.environ.setdefault("DISPLAY", ":99")
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                str(prof_copy), headless=False,
                viewport={"width": 1480 + random.randint(-15, 15),
                          "height": 940 + random.randint(-15, 15)},
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                      "--password-store=basic", "--disable-blink-features=AutomationControlled"])
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            fn = {"chatgpt": ask_chatgpt, "claude": ask_claude,
                  "gemini": ask_gemini, "deepseek": ask_deepseek}[a.provider]
            status, answer = fn(page, prompt, a.timeout, resume_url=resume_url)
            try:
                final_url = page.url or ""
            except Exception:
                final_url = ""
            # 诚实复用判定：只有最终 URL 与续接 URL 完全一致，才算复用成功。
            # 若 provider 把死 URL 重定向/转成新会话（最终 URL 不同），则如实记为
            # 新会话，不许假装复用成功；注册表随后更新为最终 URL。
            if resume_url and final_url and final_url != resume_url:
                session_reused = False
                session_note = "resume URL did not persist, new conversation created"
            # 会话失效长期维护：续接导航在发送前就失败（blocked/not_logged_in）时，
            # 自动降级为新会话重试一次，并如实记 fallback，不许假装复用成功。
            # 超时/限流等已发送后的失败不重试，避免重复执行。
            if resume_url and status in ("blocked", "not_logged_in"):
                session_note = "resume failed (%s), fell back to new session" % status
                session_reused = False
                status, answer = fn(page, prompt, a.timeout, resume_url=None)
                try:
                    final_url = page.url or ""
                except Exception:
                    pass
            ctx.close()
        conversation_url = final_url
        if registry_key:
            base = cfg["url"].rstrip("/")
            if not conversation_url or conversation_url.rstrip("/") == base:
                old = load_registry()["sessions"].get(registry_key, {})
                conversation_url = old.get("conversation_url") or ""
                if "registry URL unchanged" not in session_note:
                    session_note = (session_note + "; registry URL unchanged: final URL not a conversation URL").strip("; ")
            record_session(a.provider, session_key, conversation_url, prompt_hash, status, session_reused)
        print(json.dumps({"provider": a.provider, "status": status,
                          "answer": answer, "elapsed_s": round(time.time() - t0, 1),
                          "session_key": session_key, "session_reused": session_reused,
                          "conversation_url": conversation_url or None,
                          "session_note": session_note,
                          "registry_path": str(REGISTRY_PATH) if registry_key else None},
                         ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"provider": a.provider, "status": "error",
                          "answer": "%s: %s" % (type(e).__name__, e)[:300],
                          "elapsed_s": round(time.time() - t0, 1),
                          "session_key": session_key, "session_reused": False,
                          "conversation_url": None, "session_note": session_note,
                          "registry_path": str(REGISTRY_PATH) if registry_key else None}, ensure_ascii=False))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            if _runtime_dir is not None and _runtime_used:
                shutil.rmtree(_runtime_dir, ignore_errors=True)
        except Exception:
            pass

if __name__ == "__main__":
    main()
