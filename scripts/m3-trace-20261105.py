#!/usr/bin/env python3
"""M3 出口门第5项: 最小 trace (CHAIR-20261105-18, 2026-11-05).

每次模型调用记一行 JSONL, 字段齐全才算数:
  task_id / ts / ts_unix / model_id / latency_ms / latency_s /
  result_category (成功/429/503/超时/空/格式错) /
  http_status / returned_model / source / timeout_s / content_len / error

落盘: state/m3-exit-20261105/5-trace-20261105.jsonl
原始凭据: state/m3-exit-20261105/raw-<task_id>.json
  (http状态/耗时/返回模型/内容前120字/错误前200字; 无密钥, 无全文)

分类规则 (确定性, --verify 用同一函数重算):
  超时: 调用超 timeout_s 未返回 (含 opencode rc=124)
  429: HTTP 429; 503: HTTP 503 或其他 5xx (精确码见 http_status)
  空: HTTP 200 但内容为空, 或 opencode 无文本返回 (含 404 等其他 4xx 无可用返回)
  格式错: 要求 JSON 的调用解析失败
  成功: 其余有有效返回
用法:
  --probe   最小探针: glm-4-flash-cc 与 gemini-3.5-flash-lite 各 1 次网关调用
            (口令 ping, max_tokens=16) + Zen(opencode deepseek-v4.1-flash-free) 1 次
            共 3 个 task_id, 3 次调用, 不多烧额度
  --verify  逐条复核 trace 行 vs raw 文件: 模型/延迟/结果类别是否一致
不改任何既有脚本 (c2-verifier / gateway_canary 均不动), 不碰控制面,
不引新中间件, 不花钱 (全免费池), 不碰 7B.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

HUB = "/home/ubuntu/ai-team"
STATE = os.path.join(HUB, "state")
M3DIR = os.path.join(STATE, "m3-exit-20261105")
TRACE = os.path.join(M3DIR, "5-trace-20261105.jsonl")
OPENCODE_CFG = "/home/ubuntu/.config/opencode/opencode.json"
GATEWAY = "http://127.0.0.1:3001/v1"
CATEGORIES = ["成功", "429", "503", "超时", "空", "格式错"]
ZEN_MODEL = "opencode/deepseek-v4.1-flash-free"


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def gateway_token():
    c = json.load(open(OPENCODE_CFG))
    for p in c.get("provider", {}).values():
        opt = p.get("options") or {}
        if opt.get("apiKey"):
            return opt["apiKey"]
    return ""


def classify(http_status=None, timed_out=False, content="", expect_json=False):
    """确定性分类, 与文件头规则一致; verify 重算用同一函数."""
    if timed_out:
        return "超时"
    if http_status == 429:
        return "429"
    if http_status is not None and http_status >= 500:
        return "503"
    if http_status is not None and 400 <= http_status < 500:
        return "空"
    if not (content or "").strip():
        return "空"
    if expect_json:
        try:
            json.loads((content or "").strip().strip("`"))
        except Exception:
            return "格式错"
    return "成功"


def write_trace(rec, raw):
    os.makedirs(M3DIR, exist_ok=True)
    with open(os.path.join(M3DIR, "raw-%s.json" % rec["task_id"]), "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    with open(TRACE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(rec, ensure_ascii=False), flush=True)


def probe_gateway(model_id, task_id, timeout_s=30):
    t0 = time.monotonic()
    body = json.dumps({"model": model_id,
                       "messages": [{"role": "user", "content": "只回复口令：M3TRACE"}],
                       "max_tokens": 16, "temperature": 0}).encode()
    req = urllib.request.Request(
        GATEWAY + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + gateway_token(),
                 "Content-Type": "application/json"})
    http_status, content, returned, error, timed_out = None, "", "", "", False
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            http_status = r.status
            d = json.loads(r.read().decode() or "{}")
        ch = (d.get("choices") or [])
        msg = (ch[0].get("message") or {}) if ch else {}
        content = msg.get("content") or ""
        returned = d.get("model", "") or ""
    except urllib.error.HTTPError as e:
        http_status = e.code
        error = "HTTP Error %s: %s" % (e.code, e.reason or "")
        try:
            content = e.read().decode()[:200]
        except Exception:
            pass
    except Exception as e:
        error = "%s: %s" % (type(e).__name__, str(e)[:200])
        timed_out = "timed out" in str(e).lower() or isinstance(e, TimeoutError)
    elapsed_ms = int(round((time.monotonic() - t0) * 1000))
    cat = classify(http_status=http_status, timed_out=timed_out, content=content)
    rec = {"task_id": task_id, "ts": now_iso(), "ts_unix": int(time.time()),
           "model_id": model_id, "latency_ms": elapsed_ms,
           "latency_s": round(elapsed_ms / 1000.0, 2),
           "result_category": cat, "http_status": http_status,
           "returned_model": returned, "source": "gateway-chat-completions",
           "timeout_s": timeout_s, "content_len": len(content.strip()),
           "error": error[:200]}
    raw = {"task_id": task_id, "model_id": model_id, "http_status": http_status,
           "elapsed_ms": elapsed_ms, "timed_out": timed_out,
           "returned_model": returned, "content_snip": content.strip()[:120],
           "error": error[:200], "ts": rec["ts"]}
    write_trace(rec, raw)
    return rec


def probe_zen(task_id, timeout_s=120):
    t0 = time.monotonic()
    cmd = ["opencode", "run", "--pure", "-m", ZEN_MODEL, "--format", "json",
           "--title", "m3-trace",
           "只回答四个字符：ZENOK。不要解释，不要调用工具。"]
    timed_out, rc, answer, error = False, None, "", ""
    try:
        p = subprocess.run(cmd, cwd="/tmp", capture_output=True, text=True, timeout=timeout_s)
        rc = p.returncode
        texts = []
        for line in (p.stdout or "").splitlines():
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("type") == "text":
                texts.append((ev.get("part") or {}).get("text", ""))
        answer = " ".join(texts).strip()
        if rc != 0:
            error = "rc=%s stderr=%s" % (rc, (p.stderr or "")[:150])
    except subprocess.TimeoutExpired:
        timed_out, rc = True, 124
        error = "timeout after %ss (rc=124)" % timeout_s
    except Exception as e:
        error = "%s: %s" % (type(e).__name__, str(e)[:200])
    elapsed_ms = int(round((time.monotonic() - t0) * 1000))
    ok = (not timed_out) and rc == 0 and "ZENOK" in answer
    cat = classify(http_status=None, timed_out=timed_out, content=answer)
    if not ok and cat == "成功":
        cat = "空"
    rec = {"task_id": task_id, "ts": now_iso(), "ts_unix": int(time.time()),
           "model_id": ZEN_MODEL, "latency_ms": elapsed_ms,
           "latency_s": round(elapsed_ms / 1000.0, 2),
           "result_category": cat, "http_status": None,
           "returned_model": ZEN_MODEL if ok else "", "source": "zen-opencode-run",
           "timeout_s": timeout_s, "content_len": len(answer),
           "error": error[:200]}
    raw = {"task_id": task_id, "model_id": ZEN_MODEL, "http_status": None,
           "elapsed_ms": elapsed_ms, "timed_out": timed_out, "rc": rc,
           "returned_model": rec["returned_model"], "content_snip": answer[:120],
           "error": error[:200], "ts": rec["ts"]}
    write_trace(rec, raw)
    return rec


def verify():
    if not os.path.exists(TRACE):
        print("verify: no trace file %s" % TRACE)
        return 1
    lines = [json.loads(x) for x in open(TRACE, encoding="utf-8") if x.strip()]
    n_ok, n_fail = 0, 0
    for rec in lines:
        tid = rec.get("task_id", "?")
        raw_path = os.path.join(M3DIR, "raw-%s.json" % tid)
        problems = []
        if not os.path.exists(raw_path):
            print("FAIL %s: raw file missing %s" % (tid, raw_path))
            n_fail += 1
            continue
        raw = json.load(open(raw_path, encoding="utf-8"))
        if raw.get("model_id") != rec.get("model_id"):
            problems.append("model trace=%s raw=%s" % (rec.get("model_id"), raw.get("model_id")))
        d_ms = abs(int(rec.get("latency_ms", -1)) - int(raw.get("elapsed_ms", -2)))
        if d_ms > 1000:
            problems.append("latency diff %sms (>1000ms)" % d_ms)
        cat2 = classify(http_status=raw.get("http_status"),
                        timed_out=raw.get("timed_out", False),
                        content=raw.get("content_snip", ""))
        if rec.get("source") == "zen-opencode-run":
            # 与 probe_zen 同逻辑: rc!=0 或无 ZENOK 标记, 有文本也不算成功
            ok2 = (not raw.get("timed_out", False)) and raw.get("rc") == 0 \
                and "ZENOK" in (raw.get("content_snip") or "")
            if not ok2 and cat2 == "成功":
                cat2 = "空"
        if cat2 != rec.get("result_category"):
            problems.append("category trace=%s recomputed=%s" % (rec.get("result_category"), cat2))
        if rec.get("http_status") != raw.get("http_status"):
            problems.append("http_status trace=%s raw=%s" % (rec.get("http_status"), raw.get("http_status")))
        if rec.get("result_category") not in CATEGORIES and not str(rec.get("result_category", "")).startswith("HTTP"):
            problems.append("category not in enum: %s" % rec.get("result_category"))
        if problems:
            print("FAIL %s: %s" % (tid, "; ".join(problems)))
            n_fail += 1
        else:
            print("PASS %s: model=%s latency_ms=%s category=%s http=%s (raw 对上)"
                  % (tid, rec.get("model_id"), rec.get("latency_ms"),
                     rec.get("result_category"), rec.get("http_status")))
            n_ok += 1
    print("verify: %d PASS, %d FAIL, %d total" % (n_ok, n_fail, len(lines)))
    return 0 if n_fail == 0 and n_ok == len(lines) and len(lines) > 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    if a.probe:
        probe_gateway("glm-4-flash-cc", "m3-5-glm-20261105-01")
        time.sleep(3)
        probe_gateway("gemini-3.5-flash-lite", "m3-5-gemini-lite-20261105-01")
        time.sleep(3)
        probe_zen("m3-5-zen-20261105-01")
        return 0
    if a.verify:
        return verify()
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
