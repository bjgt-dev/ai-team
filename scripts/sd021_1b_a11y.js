#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");

const dir = process.argv[2] || "/home/ubuntu/ai-team/knowledge/sd021-iteration1b-self-20261025-0107/sd021-iteration1b";
const html = fs.readFileSync(path.join(dir, "index.html"), "utf8");
const app = fs.readFileSync(path.join(dir, "app.js"), "utf8");

const results = [];
function check(name, ok, detail) {
  results.push({ name: name, ok: !!ok, detail: detail || "" });
}
function cssVar(name) {
  const m = html.match(new RegExp("--" + name + "\\s*:\\s*(#[0-9a-fA-F]{6})"));
  return m ? m[1] : null;
}
function lum(hex) {
  const h = hex.replace("#", "");
  const c = [0, 2, 4].map(function (i) { return parseInt(h.slice(i, i + 2), 16) / 255; })
    .map(function (x) { return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); });
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}
function contrast(a, b) {
  const l1 = lum(a);
  const l2 = lum(b);
  const hi = Math.max(l1, l2);
  const lo = Math.min(l1, l2);
  return (hi + 0.05) / (lo + 0.05);
}
function ratioCheck(name, fg, bg, min) {
  if (!fg || !bg) {
    check(name, false, "missing color value");
    return;
  }
  const r = contrast(fg, bg);
  check(name, r >= min, "ratio=" + r.toFixed(2) + " min=" + min + " (" + fg + "/" + bg + ")");
}

const ink = cssVar("ink");
const muted = cssVar("muted");
const ok = cssVar("ok");
const bad = cssVar("bad");
const brandDark = cssVar("brand-dark");

check("html:lang_zh_cn", /<html[^>]+lang=["']zh-CN["']/i.test(html), "");
check("semantics:single_h1", (html.match(/<h1\b/gi) || []).length === 1, "");
check("semantics:question_h2", /<h2[^>]+id=["']quiz-stem["']/i.test(html), "");
check("semantics:buttons_type", (html.match(/<button\b/gi) || []).length === (html.match(/<button[^>]+type=["']button["']/gi) || []).length, "");
check("focus:option_visible", /\.quiz-options\s+\.option-button:focus-visible/.test(html) && /outline\s*:\s*\d+px\s+solid/i.test(html), "");
check("focus:restart_visible", /\.restart-button:focus-visible/.test(html) && /outline\s*:\s*\d+px\s+solid/i.test(html), "");
check("a11y:live_region", /aria-live=["']polite["']/i.test(html), "");
check("a11y:progressbar_role", /role=["']progressbar["']/i.test(html), "");
check("a11y:progressbar_range", /aria-valuemin=["']0["']/i.test(html) && /aria-valuemax=["']16["']/i.test(html) && /aria-valuenow=["']0["']/i.test(html), "");
check("a11y:progressbar_updates", /aria-valuenow/.test(app) && /setAttribute\(["']aria-valuenow["']/.test(app), "");

ratioCheck("contrast:ink_on_card", ink, "#ffffff", 4.5);
ratioCheck("contrast:muted_on_card", muted, "#ffffff", 4.5);
ratioCheck("contrast:correct_on_card", ok, "#ffffff", 4.5);
ratioCheck("contrast:wrong_on_card", bad, "#ffffff", 4.5);
ratioCheck("contrast:restart_label", "#ffffff", brandDark, 4.5);

const total = results.length;
const pass = results.filter(function (r) { return r.ok; }).length;
results.forEach(function (r) {
  console.log((r.ok ? "PASS " : "FAIL ") + r.name + (r.detail ? " | " + r.detail : ""));
});
console.log("total=" + total + " pass=" + pass + " fail=" + (total - pass));
process.exit(pass === total ? 0 : 1);
