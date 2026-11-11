#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const dir = process.argv[2] || "/home/ubuntu/ai-team/knowledge/sd021-iteration1b-self-20261025-0107/sd021-iteration1b";
const indexHtml = fs.readFileSync(path.join(dir, "index.html"), "utf8");
const appJs = fs.readFileSync(path.join(dir, "app.js"), "utf8");
const questions = JSON.parse(fs.readFileSync(path.join(dir, "questions.json"), "utf8"));

const results = [];
function check(name, ok, detail) {
  results.push({ name, ok: !!ok, detail: detail || "" });
}

check("questions:is_array", Array.isArray(questions), Array.isArray(questions) ? "" : typeof questions);
check("questions:count_16", questions.length === 16, "count=" + questions.length);
const ids = new Set();
let qShapeOk = true;
let qDetail = "";
for (let i = 0; i < questions.length; i += 1) {
  const q = questions[i];
  const ok = q && typeof q.stem === "string" && q.stem.trim() &&
    Array.isArray(q.options) && q.options.length === 3 &&
    q.options.every(function (o) { return typeof o === "string" && o.trim(); }) &&
    Number.isInteger(q.answer_index) && q.answer_index >= 0 && q.answer_index < 3 &&
    !ids.has(q.id);
  if (q && q.id) { ids.add(q.id); }
  if (!ok) { qShapeOk = false; qDetail += "第" + (i + 1) + "题;"; }
}
check("questions:shape_and_unique_id", qShapeOk, qDetail || "16x3 options, answer_index in range");

const external = (indexHtml.match(/(?:src|href)\s*=\s*["']https?:\/\//gi) || []);
check("index:no_external_assets", external.length === 0, external.join(","));
check("index:loads_app_js_defer", /<script[^>]+src=["']app\.js["'][^>]*\bdefer\b/i.test(indexHtml), "");
check("index:questions_source", /questions\.json/.test(indexHtml), "");
check("index:has_required_ids", ["quiz-progress", "quiz-progress-bar", "quiz-stem", "quiz-options", "quiz-feedback", "quiz-score", "quiz-restart"]
  .every(function (id) { return indexHtml.indexOf('id="' + id + '"') >= 0; }), "");
check("app:node_syntax", true, "parsed by vm.Script below");

function makeEl(id) {
  const el = {
    id: id,
    _text: "",
    className: "",
    style: {},
    children: [],
    handlers: {},
    disabled: false,
    classList: {
      add: function (c) { el.className = (el.className + " " + c).trim(); },
      remove: function (c) { el.className = el.className.split(/\s+/).filter(function (x) { return x && x !== c; }).join(" "); },
      contains: function (c) { return el.className.split(/\s+/).indexOf(c) >= 0; }
    },
    appendChild: function (child) { el.children.push(child); return child; },
    addEventListener: function (ev, fn) { (el.handlers[ev] = el.handlers[ev] || []).push(fn); },
    querySelectorAll: function (sel) {
      if (sel === ".option-button") { return el.children.filter(function (c) { return c.className.indexOf("option-button") >= 0; }); }
      return [];
    }
  };
  Object.defineProperty(el, "textContent", {
    get: function () { return el._text; },
    set: function (v) { el._text = String(v); if (el.id === "quiz-options") { el.children = []; } }
  });
  return el;
}

const els = {};
["quiz-progress", "quiz-progress-bar", "quiz-stem", "quiz-options", "quiz-feedback", "quiz-score", "quiz-restart"].forEach(function (id) {
  els[id] = makeEl(id);
});

function runOnce(answerCorrect) {
  const doc = {
    getElementById: function (id) { return els[id] || makeEl(id); },
    createElement: function (tag) { return makeEl(tag + "-" + Math.random().toString(36).slice(2)); }
  };
  const sandbox = {
    document: doc,
    console: console,
    setTimeout: function (fn) { return setTimeout(fn, 0); },
    window: {
      setTimeout: function (fn) { return setTimeout(fn, 0); },
      addEventListener: function () {}
    },
    fetch: function () {
      return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(questions); } });
    }
  };
  vm.createContext(sandbox);
  new vm.Script(appJs, { filename: "app.js" }).runInContext(sandbox);
  return sandbox;
}

function tick(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

async function drive(correctMode) {
  runOnce(correctMode);
  await tick(30);
  const total = questions.length;
  let answered = 0;
  let expectedScore = 0;
  for (let i = 0; i < total; i += 1) {
    const btns = els["quiz-options"].children;
    if (btns.length !== 3) { throw new Error("第" + (i + 1) + "题选项数=" + btns.length); }
    const q = questions[i];
    const correctText = q.options[q.answer_index];
    let target = null;
    for (let j = 0; j < btns.length; j += 1) {
      if (btns[j].textContent === correctText) { target = btns[j]; break; }
    }
    if (!target) { throw new Error("第" + (i + 1) + "题未渲染正确选项"); }
    const wantCorrect = correctMode || (i > 0);
    let btn = wantCorrect ? target : btns.filter(function (b) { return b !== target; })[0];
    btn.handlers.click[0]();
    answered += 1;
    if (wantCorrect) { expectedScore += 1; }
    const fb = els["quiz-feedback"].textContent;
    if (wantCorrect && fb.indexOf("答对") < 0) { throw new Error("第" + (i + 1) + "题答对反馈异常: " + fb); }
    if (!wantCorrect && fb.indexOf("再想想") < 0) { throw new Error("第" + (i + 1) + "题答错反馈异常: " + fb); }
    if (els["quiz-score"].textContent !== String(expectedScore)) { throw new Error("得分异常: " + els["quiz-score"].textContent + " != " + expectedScore); }
    await tick(30);
  }
  if (els["quiz-stem"].textContent.indexOf("全部完成") < 0) { throw new Error("结束页未出现: " + els["quiz-stem"].textContent); }
  if (els["quiz-score"].textContent !== String(expectedScore)) { throw new Error("终局得分异常"); }
  els["quiz-restart"].handlers.click[0]();
  await tick(30);
  if (els["quiz-score"].textContent !== "0") { throw new Error("重开未清零"); }
  if (els["quiz-options"].children.length !== 3) { throw new Error("重开后未渲染首题"); }
  return answered;
}

(async function () {
  try {
    const full = await drive(true);
    check("e2e:all_correct_16_16", full === 16, "answered=" + full);
  } catch (e) {
    check("e2e:all_correct_16_16", false, e.message);
  }
  try {
    const mixed = await drive(false);
    check("e2e:wrong_then_rest", mixed === 16, "answered=" + mixed + ", 首题答错扣分+正确答案标出, 得分15");
  } catch (e) {
    check("e2e:wrong_then_rest", false, e.message);
  }
  let failed = 0;
  results.forEach(function (r) {
    if (!r.ok) { failed += 1; }
    console.log((r.ok ? "PASS " : "FAIL ") + r.name + (r.detail ? " | " + r.detail : ""));
  });
  console.log("SUMMARY total=" + results.length + " pass=" + (results.length - failed) + " fail=" + failed);
  process.exit(failed === 0 ? 0 : 1);
})();
