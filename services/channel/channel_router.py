#!/usr/bin/env python3
"""Channel router (arm) — 定版 architecture-final-20261023 第 5 项最小实现.

职责：无记忆、不做业务决策、tool-only 的分流器。
- 业务请求：转发 arm 大管家（网关 /hooks/agent，agent=butler，独立 sessionKey）
- 轻量请求：通道层直接回答（问候/时间/状态/帮助）
边界：不改 TG 告警通道；不碰儿童项目；默认只绑定 127.0.0.1，等验收后再由 nginx 暴露。

安全：/route 需要 X-Token（state/.channel-token，600）；日志只记长度与哈希，不落原文。
"""
import hashlib
import hmac
import json
import os
import re
import signal
import sys
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

BASE = "/home/ubuntu/ai-team"
CONFIG = os.environ.get("CHANNEL_CONFIG", os.path.join(BASE, "services/channel/channel.json"))
START_TS = time.time()
STATE = {"config": None, "config_mtime": 0, "counters": {"route": 0, "business": 0, "light": 0, "forward_ok": 0, "forward_fail": 0, "auth_fail": 0}}


def load_config():
    mtime = os.path.getmtime(CONFIG)
    if STATE["config"] is None or mtime != STATE["config_mtime"]:
        with open(CONFIG, encoding="utf-8") as f:
            STATE["config"] = json.load(f)
        STATE["config_mtime"] = mtime
    return STATE["config"]


def read_secret(path):
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def log_event(rec):
    try:
        path = load_config().get("log_file", os.path.join(BASE, "logs/channel-router.jsonl"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def classify(text, cfg):
    rules = cfg["rules"]
    t = (text or "").strip()
    low = t.lower()
    for p in rules.get("business_prefixes", []):
        if t.startswith(p):
            return "business", 0.99, f"prefix:{p}"
    for p in rules.get("light_prefixes", []):
        if t.startswith(p):
            return "light", 0.99, f"light_prefix:{p}"
    if t.startswith("{"):
        try:
            obj = json.loads(t)
            if isinstance(obj, dict) and ({"decision_id", "task_id", "contract_id"} & set(obj)):
                return "business", 0.98, "structured:contract"
        except Exception:
            pass
    for kw in rules.get("business_keywords", []):
        if kw in t:
            return "business", 0.9, f"keyword:{kw}"
    if re.search(r"(现在|当前)?(几点|时间|日期|星期|几号)", t):
        return "light", 0.95, "light:time"
    for kw in rules.get("light_keywords", []):
        if low == kw or (len(kw) > 1 and kw in t):
            return "light", 0.85, f"light_keyword:{kw}"
    return "business", 0.5, "default:business"


def light_answer(text, cfg):
    t = (text or "").strip()
    low = t.lower()
    if t.startswith("/help") or t.startswith("/帮助") or low in ("help", "?"):
        return "通道指令：/help 帮助；/ping 连通性；/status 通道状态。业务请求请直接描述或加 /task 前缀。"
    if t.startswith("/ping"):
        return "pong"
    if t.startswith("/status") or t.startswith("/状态"):
        c = STATE["counters"]
        return (f"channel ok；uptime={int(time.time() - START_TS)}s；"
                f"route={c['route']} business={c['business']} light={c['light']} "
                f"forward_ok={c['forward_ok']} forward_fail={c['forward_fail']}")
    if re.search(r"(现在|当前)?(几点|时间|日期|星期|几号)", t):
        return datetime.now().astimezone().strftime("现在时间：%Y-%m-%d %H:%M:%S %Z")
    if low in ("你好", "您好", "hi", "hello"):
        return "你好，我是通道分流器。业务请求会转给大管家，轻量问题我直接回答。"
    if low in ("谢谢", "多谢", "thanks", "thank you"):
        return "不客气。"
    if low in ("再见", "拜拜", "bye"):
        return "再见。"
    return "已收到轻量请求。"


def forward(text, cfg, trace_id):
    h = cfg["hooks"]
    token = read_secret(h["token_file"])
    body = json.dumps({
        "message": text,
        "agentId": h.get("agent_id", "butler"),
        "sessionKey": h.get("session_key", "agent:butler:channel"),
        "name": "channel-router",
        "idempotencyKey": f"channel:{trace_id}",
        "deliver": False,
    }).encode()
    req = Request(h["url"], data=body, method="POST", headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
    })
    t0 = time.time()
    try:
        with urlopen(req, timeout=h.get("timeout_s", 30)) as r:
            payload = r.read(4000).decode(errors="replace")
            elapsed = int((time.time() - t0) * 1000)
            return True, r.status, payload, elapsed
    except HTTPError as e:
        body_txt = ""
        try:
            body_txt = e.read(500).decode(errors="replace")
        except Exception:
            pass
        return False, e.code, body_txt, int((time.time() - t0) * 1000)
    except (URLError, TimeoutError, OSError) as e:
        return False, 0, f"{type(e).__name__}: {e}", int((time.time() - t0) * 1000)


class Handler(BaseHTTPRequestHandler):
    server_version = "channel-router/0.1"

    def log_message(self, *args):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/health":
            return self._json(200, {"ok": True, "ts": now_iso(), "enabled": load_config().get("enabled", False)})
        if u.path == "/stats":
            return self._json(200, {"ok": True, "uptime_s": int(time.time() - START_TS), "counters": STATE["counters"]})
        return self._json(404, {"error": "not_found"})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/route":
            return self._json(404, {"error": "not_found"})
        cfg = load_config()
        if not cfg.get("enabled", False):
            return self._json(503, {"error": "channel_disabled"})
        token = read_secret(cfg["token_file"])
        if not hmac.compare_digest(self.headers.get("X-Token", ""), token):
            STATE["counters"]["auth_fail"] += 1
            return self._json(403, {"error": "forbidden"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n > cfg["limits"]["max_body_bytes"]:
                return self._json(413, {"error": "body_too_large"})
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json(400, {"error": "bad_json"})
        text = str(data.get("text", ""))[: cfg["limits"]["max_text_chars"]]
        source = str(data.get("source", "unknown"))[:32]
        if not text.strip():
            return self._json(400, {"error": "empty_text"})
        trace_id = uuid.uuid4().hex[:16]
        t0 = time.time()
        decision, confidence, rule = classify(text, cfg)
        STATE["counters"]["route"] += 1
        STATE["counters"][decision] += 1
        result = {"trace_id": trace_id, "decision": decision, "confidence": confidence, "rule": rule, "source": source}
        if decision == "light":
            result["reply"] = light_answer(text, cfg)
            result["handled_at"] = "channel"
        else:
            ok, status, payload, elapsed = forward(text, cfg, trace_id)
            if ok:
                STATE["counters"]["forward_ok"] += 1
                result.update({"handled_at": "butler", "forward_status": status, "forward_ms": elapsed})
                try:
                    result["upstream"] = json.loads(payload)
                except Exception:
                    result["upstream_raw"] = payload[:300]
            else:
                STATE["counters"]["forward_fail"] += 1
                result.update({"handled_at": "failed", "forward_status": status,
                               "forward_ms": elapsed, "error": payload[:300],
                               "fallback": "tg_relay"})
        result["latency_ms"] = int((time.time() - t0) * 1000)
        log_event({"ts": now_iso(), "trace_id": trace_id, "source": source, "decision": decision,
                   "confidence": confidence, "rule": rule, "text_len": len(text),
                   "text_sha256_16": hashlib.sha256(text.encode()).hexdigest()[:16],
                   "handled_at": result["handled_at"], "forward_status": result.get("forward_status"),
                   "latency_ms": result["latency_ms"]})
        return self._json(200 if result["handled_at"] != "failed" else 502, result)


def main():
    cfg = load_config()
    srv = ThreadingHTTPServer((cfg["bind"], int(cfg["port"])), Handler)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print(f"channel-router listening on {cfg['bind']}:{cfg['port']}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
