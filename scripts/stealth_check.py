#!/usr/bin/env python3
# 指纹自检 stealth_check.py（2026-10-20）：invisible_playwright 内核 + https://bot.sannysoft.com
# 输出关键行：navigator.webdriver / WebGL vendor+renderer / Canvas / Sannysoft 表格关键判定行。
import json, sys, time
from invisible_playwright import InvisiblePlaywright

OUT = {"kernel": "invisible_playwright (patched Firefox)"}
with InvisiblePlaywright(seed=20261020, headless=True, humanize=True) as browser:
    page = browser.new_page()
    OUT["webdriver_js"] = page.evaluate("navigator.webdriver")
    page.goto("https://bot.sannysoft.com/", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(9000)
    OUT["webdriver_after"] = page.evaluate("navigator.webdriver")
    OUT["user_agent"] = page.evaluate("navigator.userAgent")
    OUT["platform"] = page.evaluate("navigator.platform")
    OUT["languages"] = page.evaluate("navigator.languages")
    gl = page.evaluate("""() => {
      const c = document.createElement('canvas');
      const g = c.getContext('webgl') || c.getContext('experimental-webgl');
      if (!g) return null;
      const ext = g.getExtension('WEBGL_debug_renderer_info');
      return {vendor: ext ? g.getParameter(ext.UNMASKED_VENDOR_WEBGL) : g.getParameter(g.VENDOR),
              renderer: ext ? g.getParameter(ext.UNMASKED_RENDERER_WEBGL) : g.getParameter(g.RENDERER)};
    }""")
    OUT["webgl"] = gl
    OUT["canvas_hash_prefix"] = page.evaluate("""() => {
      const c = document.createElement('canvas'); c.width = 220; c.height = 40;
      const x = c.getContext('2d'); x.font = '14px Arial'; x.fillText('fp-check-123', 4, 20);
      const d = c.toDataURL(); return d.slice(0, 40) + '...len=' + d.length;
    }""")
    rows = page.evaluate("""() => Array.from(document.querySelectorAll('tr')).map(tr => tr.innerText.replace(/\\s+/g,' ').trim()).filter(t => t.length > 0)""")
    keys = ["webdriver", "chrome", "firefox", "webgl", "canvas", "plugins", "languages",
            "permissions", "user agent", "screen", "cpu", "memory", "fonts", "audio"]
    OUT["sannysoft_rows"] = [r for r in rows if any(k in r.lower() for k in keys)][:28]
    page.screenshot(path="/tmp/sannysoft.png", full_page=True)
print(json.dumps(OUT, ensure_ascii=False, indent=1))
