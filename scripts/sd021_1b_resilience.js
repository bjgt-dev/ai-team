#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const dir = process.argv[2] || "/home/ubuntu/ai-team/knowledge/sd021-iteration1b-self-20261025-0107/sd021-iteration1b";
const appJs = fs.readFileSync(path.join(dir, "app.js"), "utf8");

const results = [];
function check(name, ok, detail) {
  results.push({ name: name, ok: !!ok, detail: detail || "" });
}

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
    setAttribute: function (k, v) { el[k] = v; },
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

const ids = ["quiz-progress", "quiz-progress-bar", "quiz-track", "quiz-stem", "quiz-options", "quiz-feedback", "quiz-score", "quiz-restart"];

function boot(fetchImpl) {
  const els = {};
  ids.forEach(function (id) { els[id] = makeEl(id); });
  const sandbox = {
    document: {
      getElementById: function (id) { return els[id] || makeEl(id); },
      createElement: function (tag) { return makeEl(tag + "-" + Math.random().toString(36).slice(2)); }
    },
    console: console,
    setTimeout: function (fn) { return setTimeout(fn, 0); },
    window: { setTimeout: function (fn) { return setTimeout(fn, 0); } },
    fetch: fetchImpl
  };
  vm.createContext(sandbox);
  new vm.Script(appJs, { filename: "app.js" }).runInContext(sandbox);
  return els;
}

function tick(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

function assertFailScreen(els, label) {
  const stem = els["quiz-stem"].textContent;
  const feedback = els["quiz-feedback"].textContent;
  const options = els["quiz-options"].children;
  const ok = stem.indexOf("题目加载失败") >= 0 &&
    feedback.indexOf("请通过本地服务器打开页面") >= 0 &&
    options.length === 0;
  check("offline:" + label, ok,
    "stem=" + JSON.stringify(stem) + " feedback=" + JSON.stringify(feedback) + " options=" + options.length);
}

async function scenario(name, fetchImpl, label) {
  const els = boot(fetchImpl);
  await tick(50);
  assertFailScreen(els, label || name);
}

(async function () {
  await scenario("network_reject", function () { return Promise.reject(new TypeError("Failed to fetch")); });
  await scenario("http_500", function () { return Promise.resolve({ ok: false, status: 500, json: function () { return Promise.resolve([]); } }); });
  await scenario("invalid_json", function () { return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.reject(new SyntaxError("Unexpected token")); } }); });
  await scenario("empty_array", function () { return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve([]); } }); });

  let failed = 0;
  results.forEach(function (r) {
    if (!r.ok) { failed += 1; }
    console.log((r.ok ? "PASS " : "FAIL ") + r.name + (r.detail ? " | " + r.detail : ""));
  });
  console.log("total=" + results.length + " pass=" + (results.length - failed) + " fail=" + failed);
  process.exit(failed === 0 ? 0 : 1);
})();
