#!/usr/bin/env python3
# ds_token_keeper.py — DeepSeek 反代 token 自愈守护（2026-10-22，董事长令「定个机制，不想一直登录」）
# 原理：登录态的唯一真源 = 枢纽常驻浏览器档案的活页面；token 文件/config 只是缓存。
# 本脚本定期从活页面 CDP 直读最新 userToken → 官方接口验明账号 → 与 config 对表，
# 变了才写回（先备份+记 sha256）、清掉 password 字段回到 token-only、重启 ds2api、冒烟实测。
# 红线：绝不做密码登录/自动登录；活页面未登录/被封/撞验证时只记状态、不动任何东西，等董事长登一次即可。
# 用法：ds_token_keeper.py --port 9228 [--config PATH] [--state-file PATH] [--container ds2api-smoke] [--check-only]
import argparse, hashlib, json, subprocess, sys, time, urllib.request
from pathlib import Path

LOG = Path("/home/ubuntu/ai-team/state/ds-token-keeper.log")

def log(msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}"
    print(line)
    try:
        with LOG.open("a") as f: f.write(line + "\n")
    except Exception: pass

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:16]

def extract_token(port):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        for ctx in browser.contexts:
            for page in ctx.pages:
                if "chat.deepseek.com" in (page.url or ""):
                    raw = page.evaluate("() => localStorage.getItem('userToken')")
                    if not raw: return None
                    try:
                        d = json.loads(raw)
                        if isinstance(d, dict): return d.get("value") or d.get("token")
                    except Exception: pass
                    return raw
        return None

def verify(token):
    req = urllib.request.Request("https://chat.deepseek.com/api/v0/users/current",
                                 headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.load(r)
    except Exception as e:
        return None, f"http_error:{type(e).__name__}"
    if d.get("code") == 0:
        biz = ((d.get("data") or {}).get("biz_data") or {})
        return biz.get("email"), "ok"
    return None, f"api_code:{d.get('code')}"

def reload_page(port):
    # token 被拒时先刷新活页面：cookie 还活着的话网页会自己换发新 token（自愈第一招）
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        for ctx in browser.contexts:
            for page in ctx.pages:
                if "chat.deepseek.com" in (page.url or ""):
                    page.reload(wait_until="domcontentloaded", timeout=60000)
                    time.sleep(7)
                    return "sign_in" in (page.url or "")
    return False

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--config", default="/home/ubuntu/ai-team/services/ds2api/config.json")
    ap.add_argument("--state-file", default="")
    ap.add_argument("--container", default="ds2api-smoke")
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()
    try:
        tok = extract_token(a.port)
    except Exception as e:
        log(f"port={a.port} status=cdp_unreachable err={type(e).__name__}"); return 2
    if not tok:
        log(f"port={a.port} status=not_logged_in"); return 3
    email, st = verify(tok)
    if st != "ok" and not a.check_only:
        # 先自愈：刷新页面让网页换发 token；cookie 也死了才判需登录
        try:
            kicked = reload_page(a.port)
            tok2 = extract_token(a.port)
            if tok2:
                email2, st2 = verify(tok2)
                if st2 == "ok":
                    log(f"port={a.port} status=self_healed_by_reload kicked_to_signin={kicked}")
                    tok, email, st = tok2, email2, st2
        except Exception as e:
            log(f"port={a.port} status=reload_retry_failed err={type(e).__name__}")
    if st != "ok":
        log(f"port={a.port} status=token_rejected detail={st}"); return 4
    cfgp = Path(a.config)
    cfg = json.load(open(cfgp))
    acct = cfg["accounts"][0]
    changed = (acct.get("token") != tok) or (acct.get("email") != email) or bool(acct.get("password"))
    log(f"port={a.port} status=valid email={email} changed={changed} check_only={a.check_only}")
    if a.check_only or not changed: return 0
    bak = f"{cfgp}.bak-keeper-{time.strftime('%Y%m%d-%H%M%S')}"
    subprocess.run(["cp", str(cfgp), bak], check=True)
    log(f"config_backup={bak} sha_before={sha(cfgp)}")
    acct["token"] = tok; acct["email"] = email; acct["password"] = ""
    json.dump(cfg, open(cfgp, "w"), ensure_ascii=False, indent=2)
    subprocess.run(["chmod", "600", str(cfgp)])
    if a.state_file:
        Path(a.state_file).write_text(tok); subprocess.run(["chmod", "600", a.state_file])
    subprocess.run(["sudo", "-n", "docker", "restart", a.container], check=True,
                   stdout=subprocess.DEVNULL)
    time.sleep(5)
    key = cfg["keys"][0]
    ok = {}
    for m in ("deepseek-chat", "deepseek-reasoner"):
        req = urllib.request.Request("http://127.0.0.1:5001/v1/chat/completions",
            data=json.dumps({"model": m, "messages": [{"role": "user", "content": "ping, reply ok"}], "max_tokens": 16}).encode(),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = json.load(r)
                ct = ((body.get("usage") or {}).get("completion_tokens"))
                ok[m] = r.status if ct else "empty_completion"
        except Exception as e: ok[m] = f"err:{type(e).__name__}"
    log(f"applied email={email} sha_after={sha(cfgp)} smoke={ok}")
    if all(v == 200 for v in ok.values()): return 0
    if any(v == "empty_completion" for v in ok.values()):
        log("status=account_suspended_or_throttled (200 but 0 completion tokens)")
        return 6
    return 5

if __name__ == "__main__":
    sys.exit(main())
