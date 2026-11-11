#!/usr/bin/env python3
"""P2-9 端到端验收 canary（三条）：TG 通道 / 主聊时延 / 契约全链.

只读与验收性质：
- tg: 网关健康 + bot getMe（不发消息；--tg-send 且给出 chat_id 才发一条纯文本测试，默认关）
- latency: 向网关 hooks/agent 提交一条只读测试题，记录「提交→受理（runId 返回）」时延
- contract: 投一条固定文本 canary 契约，观察 pending→claimed→done 与 verify.py 结果；超时移入 quarantine，不删除

输出：state/canary-status.json（最新一次）+ logs/canary-e2e.log（追加）
用法：python3 scripts/canary_e2e.py {tg|latency|contract|all} [--tg-send --chat-id N] [--contract-wait 90]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
import uuid
from datetime import datetime

BASE = "/home/ubuntu/ai-team"
STATE = os.path.join(BASE, "state/canary-status.json")
LOG = os.path.join(BASE, "logs/canary-e2e.log")
GATEWAY = "http://127.0.0.1:18789"
CONTAINER = "openclaw-official-openclaw-gateway-1"
HOOKS_TOKEN = os.path.join(BASE, "state/.hooks-token")


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(rec):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def http(method, url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
        return r.status, body, int((time.time() - t0) * 1000)


def tg_token():
    p = subprocess.run(["docker", "exec", CONTAINER, "cat", "/home/node/.openclaw/.telegram-bot-token"],
                       capture_output=True, text=True, timeout=20)
    return p.stdout.strip() if p.returncode == 0 else ""


def canary_tg(send=False, chat_id=None):
    out = {"name": "tg", "ts": now()}
    try:
        st, body, ms = http("GET", GATEWAY + "/health", timeout=10)
        out["gateway_health"] = {"status": st, "ms": ms, "body": body[:80]}
    except Exception as e:
        out["gateway_health"] = {"status": 0, "error": type(e).__name__}
    tok = tg_token()
    if not tok:
        out["bot"] = {"ok": False, "reason": "token_unreadable"}
    else:
        try:
            st, body, ms = http("GET", f"https://api.telegram.org/bot{tok}/getMe", timeout=15)
            d = json.loads(body)
            out["bot"] = {"ok": bool(d.get("ok")), "username": (d.get("result") or {}).get("username"),
                          "status": st, "ms": ms}
        except Exception as e:
            out["bot"] = {"ok": False, "reason": type(e).__name__}
        if send and chat_id:
            payload = json.dumps({"chat_id": chat_id, "text": "CANARY_TG_OK（只读验收，请忽略）"}).encode()
            try:
                st, body, ms = http("POST", f"https://api.telegram.org/bot{tok}/sendMessage",
                                    data=payload, headers={"Content-Type": "application/json"}, timeout=20)
                out["send"] = {"status": st, "ok": json.loads(body).get("ok")}
            except Exception as e:
                out["send"] = {"ok": False, "reason": type(e).__name__}
    out["pass"] = bool(out.get("gateway_health", {}).get("status") == 200 and out.get("bot", {}).get("ok"))
    return out


def canary_latency():
    out = {"name": "latency", "ts": now()}
    try:
        tok = open(HOOKS_TOKEN, encoding="utf-8").read().strip()
    except Exception as e:
        out.update({"pass": False, "error": f"hooks_token:{type(e).__name__}"})
        return out
    body = json.dumps({
        "message": "只回答四个字：链路正常。不要解释，不要代码块。",
        "agentId": "main",
        "sessionKey": "agent:main:canary-latency",
        "name": "canary-e2e",
        "idempotencyKey": "canary-latency-" + uuid.uuid4().hex[:8],
        "deliver": False,
    }).encode()
    try:
        st, payload, ms = http("POST", GATEWAY + "/hooks/agent", data=body,
                               headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"},
                               timeout=60)
        out["accept"] = {"status": st, "ms": ms, "upstream": payload[:200]}
        out["metric"] = "提交→受理（runId 返回）时延，不含模型生成；首回复时延需会话回读口径（待补）"
        out["pass"] = st == 200 and ms < 10000
    except Exception as e:
        out.update({"pass": False, "error": f"hooks:{type(e).__name__}"})
    return out


def validate_contract(path):
    p = subprocess.run(["python3", os.path.join(BASE, "scripts/validate_contract.py"), path],
                       capture_output=True, text=True, timeout=30)
    return p.returncode == 0, (p.stdout + p.stderr).strip()[:300]


def canary_contract(wait_s=90):
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    tid = "canary-e2e-contract-" + ts
    out = {"name": "contract", "ts": now(), "task_id": tid}
    c = {
        "task_id": tid,
        "task_type": "llm_generate",
        "source": "ceo_canary_e2e",
        "objective": "只输出一行固定文本：CANARY_CONTRACT_OK。不要解释，不要 Markdown 代码块。",
        "output_file": "artifacts/canary-contract.md",
        "acceptance_criteria": [
            {"type": "file_exists", "target": "artifacts/canary-contract.md"},
            {"type": "file_min_size", "target": "artifacts/canary-contract.md", "expected": 5},
        ],
        "timeout_s": 120,
        "max_retries": 0,
        "idempotency_key": tid,
        "evidence_required": ["receipts.jsonl", "manifest.json", "acceptance_self_check.json"],
        "created_at": now(),
        "negative_constraints": [{"id": "NC-01", "source_text": "不得对外发送或公开发布", "rule": "仅输出固定文本，不调用外发通道", "acceptance_check": "artifacts/canary-contract.md 行数=1 且不含 http 链接"}],
    }
    tmp = os.path.join(BASE, "staging", tid + ".json")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)
    ok, msg = validate_contract(tmp)
    out["validate"] = {"pass": ok, "msg": msg}
    if not ok:
        out["pass"] = False
        return out
    pend = os.path.join(BASE, "contracts/pending/guangtou", tid + ".json")
    shutil.copy(tmp, pend)
    out["queued"] = pend
    deadline = time.time() + wait_s
    claimed = done = False
    while time.time() < deadline:
        for stage in ("claimed", "done"):
            for root, _, files in os.walk(os.path.join(BASE, "contracts", stage)):
                if tid + ".json" in files:
                    if stage == "claimed":
                        claimed = True
                    if stage == "done":
                        done = True
        if done:
            break
        time.sleep(5)
    out.update({"claimed": claimed, "done": done})
    if done:
        bundle = os.path.join(BASE, "inbox", tid)
        contract_path = os.path.join(BASE, "contracts/done", tid + ".json")
        ver = subprocess.run(["python3", os.path.join(BASE, "scripts/verify.py"), bundle, contract_path],
                             capture_output=True, text=True, timeout=60)
        verdict = ""
        try:
            verdict = json.loads(ver.stdout).get("verdict", "")
        except Exception:
            pass
        out["verify"] = {"rc": ver.returncode, "verdict": verdict,
                         "out": (ver.stdout + ver.stderr).strip()[:300]}
        out["pass"] = ver.returncode == 0 and verdict == "PASS"
    else:
        q = os.path.join(BASE, "contracts/quarantine", tid)
        os.makedirs(q, exist_ok=True)
        for p in [pend] + [os.path.join(BASE, "contracts/claimed/guangtou", tid + ".json")]:
            if os.path.exists(p):
                shutil.move(p, os.path.join(q, os.path.basename(p)))
        out["quarantined"] = q
        out["pass"] = False
        out["reason"] = "未在等待期内被认领/完成（工人池当前无在线工人）"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("which", choices=["tg", "latency", "contract", "all"])
    ap.add_argument("--tg-send", action="store_true")
    ap.add_argument("--chat-id", type=int)
    ap.add_argument("--contract-wait", type=int, default=90)
    a = ap.parse_args()
    res = {"ts": now(), "round": "P2-9"}
    if a.which in ("tg", "all"):
        res["tg"] = canary_tg(a.tg_send, a.chat_id)
    if a.which in ("latency", "all"):
        res["latency"] = canary_latency()
    if a.which in ("contract", "all"):
        res["contract"] = canary_contract(a.contract_wait)
    try:
        prev = json.load(open(STATE, encoding="utf-8"))
        hist = prev.get("history", [])
    except Exception:
        hist = []
    flat = {k: v for k, v in res.items() if k != "history"}
    hist = (hist + [flat])[-20:]
    res["history"] = hist
    res["all_pass"] = all(v.get("pass") for k, v in res.items() if k in ("tg", "latency", "contract")) if any(k in res for k in ("tg","latency","contract")) else False
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    log(res)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
