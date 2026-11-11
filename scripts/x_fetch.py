#!/usr/bin/env python3
"""Fetch X (twitter) search results with a logged-in browser session.

Cookie file: /home/ubuntu/ai-team/state/x-cookies.json
  {"cookies": [{"name": "auth_token", "value": "...", "domain": ".x.com",
                "path": "/"}, {"name": "ct0", "value": "...", ...}]}
The file is created by a browser task that logs into x.com with the saved
Vault login and exports the session cookies. This script never logs in
itself and never asks for a password.
Output: JSON list of {"key": "x:<status_id>", "text": "..."} on stdout.
Exit 0 = ok, 2 = cookie file missing, 3 = session expired / login required.
"""
import json
import os
import sys

COOKIE_FILE = "/home/ubuntu/ai-team/state/x-cookies.json"
QUERIES = ["AI agent startup", "free AI API", "indie hacker AI"]
PER_QUERY = 4


def main():
    if not os.path.exists(COOKIE_FILE):
        print("x-cookies.json missing: X login not done yet (needs browser task)",
              file=sys.stderr)
        return 2
    try:
        cookies = json.load(open(COOKIE_FILE)).get("cookies", [])
    except Exception as e:
        print(f"x-cookies.json unreadable: {e}", file=sys.stderr)
        return 2
    if not any(c.get("name") == "auth_token" for c in cookies):
        print("x-cookies.json has no auth_token: session incomplete", file=sys.stderr)
        return 2
    from playwright.sync_api import sync_playwright
    out = []
    seen = set()
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = b.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            viewport={"width": 1280, "height": 900})
        ctx.add_cookies([{**c, "domain": c.get("domain", ".x.com"),
                          "path": c.get("path", "/")} for c in cookies])
        pg = ctx.new_page()
        try:
            for q in QUERIES:
                try:
                    pg.goto(f"https://x.com/search?q={q.replace(' ', '%20')}"
                            "&src=typed_query&f=live",
                            timeout=60000)
                    pg.wait_for_selector("article", timeout=30000)
                    pg.wait_for_timeout(3000)
                    for art in pg.locator("article").all()[:PER_QUERY]:
                        try:
                            link = art.locator("a[href*='/status/']").first
                            href = link.get_attribute("href") or ""
                            sid = href.rstrip("/").split("/status/")[-1].split("/")[0]
                            if not sid or not sid.isdigit() or sid in seen:
                                continue
                            seen.add(sid)
                            text = (art.inner_text() or "").replace("\n", " ").strip()
                            text = " ".join(text.split())[:200]
                            if len(text) < 20:
                                continue
                            out.append({"key": f"x:{sid}",
                                        "text": f"{text} | https://x.com/i/status/{sid}"})
                        except Exception:
                            continue
                except Exception as e:
                    print(f"x query {q!r} failed: {e}", file=sys.stderr)
                    continue
        finally:
            b.close()
    if not out:
        # Distinguish "no results" from "logged out": re-check home page.
        print("x: no tweets scraped (session may be expired)", file=sys.stderr)
        return 3
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
