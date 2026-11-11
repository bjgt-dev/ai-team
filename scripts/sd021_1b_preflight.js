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
function px(block, prop) {
  const m = block.match(new RegExp(prop + "\\s*:\\s*(\\d+)px", "i"));
  return m ? Number(m[1]) : null;
}
function ruleBlock(selector) {
  const i = html.indexOf(selector);
  if (i < 0) return "";
  const open = html.indexOf("{", i);
  const close = html.indexOf("}", open);
  return open >= 0 && close > open ? html.slice(open + 1, close) : "";
}

const viewport = /<meta[^>]+name=["']viewport["'][^>]+content=["'][^"']*width=device-width[^"']*["']/i.test(html);
check("viewport:device_width", viewport, viewport ? "" : "missing viewport meta");

const media = /@media\s*\(max-width:\s*\d+px\)/i.test(html);
check("css:responsive_media_query", media, media ? "" : "no max-width media query");

const optionBlock = ruleBlock(".quiz-options .option-button");
const optionMin = px(optionBlock, "min-height");
const optionPad = px(optionBlock, "padding");
check("touch:option_min_height_44", optionMin !== null && optionMin >= 44,
  "min-height=" + optionMin + "px");
check("touch:option_padding", optionPad !== null && optionPad >= 12,
  "padding=" + optionPad + "px");

const restartBlock = ruleBlock(".restart-button");
const restartMin = px(restartBlock, "min-height");
check("touch:restart_min_height_44", restartMin !== null && restartMin >= 44,
  "min-height=" + restartMin + "px");

const cardBlock = ruleBlock(".quiz-card");
check("layout:card_max_width_fluid", /width\s*:\s*min\(\s*\d+px\s*,\s*100%\s*\)/i.test(cardBlock),
  cardBlock.replace(/\s+/g, " ").trim().slice(0, 120));

const external = (html.match(/(?:src|href)\s*=\s*["']https?:\/\//gi) || []);
check("network:no_external_assets", external.length === 0, external.join(","));

const reduced = /@media\s*\(prefers-reduced-motion:\s*reduce\)/i.test(html);
check("a11y:reduced_motion", reduced, reduced ? "" : "missing prefers-reduced-motion");

const lockGuard = /if\s*\(\s*state\.locked\s*\)\s*\{\s*return\s*;?\s*\}/.test(app);
check("input:single_tap_lock", lockGuard, lockGuard ? "" : "missing state.locked guard");

const disableAll = /\.disabled\s*=\s*true/.test(app) && /querySelectorAll\(["']\.option-button["']\)/.test(app);
check("input:disable_options_after_answer", disableAll, disableAll ? "" : "missing disable loop");

const weakNet = /加载失败/.test(app) && /fetch\(["']questions\.json["']/.test(app) && /\.catch\(/.test(app);
check("network:weak_failure_message", weakNet, weakNet ? "" : "missing fetch failure path");

const restartReset = /function\s+restart\(\)[\s\S]*?state\.index\s*=\s*0[\s\S]*?state\.score\s*=\s*0[\s\S]*?state\.locked\s*=\s*false/.test(app);
check("state:restart_resets", restartReset, restartReset ? "" : "restart reset incomplete");

const finish100 = /function\s+finish\(\)[\s\S]*?bar\.style\.width\s*=\s*["']100%["']/.test(app);
check("state:finish_progress_100", finish100, finish100 ? "" : "finish does not set 100%");

const total = results.length;
const pass = results.filter(function (r) { return r.ok; }).length;
results.forEach(function (r) {
  console.log((r.ok ? "PASS" : "FAIL") + " " + r.name + (r.detail ? " | " + r.detail : ""));
});
console.log("total=" + total + " pass=" + pass + " fail=" + (total - pass));
process.exit(pass === total ? 0 : 1);
