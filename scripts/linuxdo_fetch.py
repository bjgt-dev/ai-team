#!/usr/bin/env python3
"""Fetch Linux Do 福利羊毛 (category 36, slug welfare) latest topics.

Why Playwright: linux.do sits behind Cloudflare; plain curl gets 403
"Just a moment..." even via residential exits. Real headless Chromium
returns HTTP 200 with the public /c/welfare/36.json payload.
No login needed for public topics; logged-in-only content is out of scope.
Output: JSON list of {"key": "linuxdo:<id>", "text": "..."} on stdout.
Exit 0 = ok (list may be empty), 1 = fetch failed.
"""
import json
import sys

URL = "https://linux.do/c/welfare/36.json"
SKIP_IDS = {30184}  # 关于“福利羊毛”类别 (pinned meta post, not a deal)


def main():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = b.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
        pg = ctx.new_page()
        try:
            r = pg.goto(URL, timeout=60000)
            pg.wait_for_timeout(4000)
            if not r or r.status != 200:
                print(f"linuxdo http={r.status if r else None}", file=sys.stderr)
                return 1
            d = json.loads(pg.inner_text("body"))
        except Exception as e:
            print(f"linuxdo fetch error: {e}", file=sys.stderr)
            return 1
        finally:
            b.close()
    topics = d.get("topic_list", {}).get("topics", [])
    out = []
    for t in topics:
        tid = t.get("id")
        if not tid or tid in SKIP_IDS:
            continue
        title = (t.get("title") or "").strip()
        if not title or title.startswith("关于"):
            continue
        out.append({
            "key": f"linuxdo:{tid}",
            "text": f"{title} (replies:{t.get('reply_count', 0)} likes:{t.get('like_count', 0)}) | https://linux.do/t/{tid}",
        })
        if len(out) >= 10:
            break
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
