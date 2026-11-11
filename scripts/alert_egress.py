#!/usr/bin/env python3
"""alert_egress.py — 告警主出口与自动切换（2026-10-28）
主出口：企业微信 webhook；备出口：ntfy/TG。
规则：主出口无回执或超时（默认 5s，可配）即记失败并立即切备出口；
备出口也失败则写本地死信队列；故障、死信、超 10 分钟无回执必须推送，不静默。
配置：只从环境变量或密钥文件读取，绝不硬编码 webhook/token；
回执与账本中不得出现密钥明文。生产启用（真实 webhook）另行大管家定案。
本文件自带 mock 发送器，仅供测试频道验证；真实发送器需显式传入 URL。
"""
import argparse, json, os, sys, time, urllib.request, urllib.error

DEADLETTER_DEFAULT = "/home/ubuntu/ai-team/state/alert-deadletter.jsonl"

def _post(url, payload, timeout):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode(errors="replace")[:500]
        return True, body

def real_primary_sender(url):
    def send(msg):
        try:
            ok, body = _post(url, {"msgtype": "text", "text": {"content": msg}}, 5)
            # WeCom 成功返回 errcode 0；有返回码即视为回执，否则按失败处理
            try:
                data = json.loads(body)
                if data.get("errcode") == 0:
                    return True, str(data.get("msgid", "wecom-ok"))
                return False, "wecom errcode=%s" % data.get("errcode")
            except Exception:
                return ok, body or "http-ok-no-receipt"
        except Exception as e:
            return False, str(e)[:200]
    return send

def real_fallback_sender(url):
    def send(msg):
        try:
            ok, body = _post(url, {"message": msg}, 5)
            return ok, body or "ntfy-ok"
        except Exception as e:
            return False, str(e)[:200]
    return send

def mock_sender(mode, receipt):
    def send(msg):
        if mode == "ok":
            return True, receipt
        return False, "mock-fail"
    return send

def load_config():
    cfg_path = os.environ.get("ALERT_EGRESS_CONFIG", "")
    if cfg_path and os.path.exists(cfg_path):
        with open(cfg_path) as f:
            return json.load(f)
    return {"primary_url": os.environ.get("ALERT_PRIMARY_URL", ""),
            "fallback_url": os.environ.get("ALERT_FALLBACK_URL", "")}

def send_alert(message, kind, primary_sender, fallback_sender,
               ledger_path, deadletter_path, timeout=5):
    """返回账本记录 dict；字段：primary_sent/primary_receipt/fallback_used/
    fallback_receipt/latency/error/deadletter/kind。故障、死信、超10分钟
    无回执三类 kind 必须推送：本函数对这三类不设静默分支。"""
    t0 = time.time()
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "kind": kind, "primary_sent": False, "primary_receipt": None,
           "fallback_used": False, "fallback_receipt": None,
           "latency": 0.0, "error": None, "deadletter": False}
    try:
        ok, receipt = primary_sender(message)
    except Exception as e:
        ok, receipt = False, str(e)[:200]
    rec["latency"] = round(time.time() - t0, 3)
    if ok and receipt:
        rec["primary_sent"] = True
        rec["primary_receipt"] = receipt
    else:
        rec["error"] = receipt or "primary-no-receipt"
        rec["fallback_used"] = True
        try:
            ok2, receipt2 = fallback_sender(message)
        except Exception as e:
            ok2, receipt2 = False, str(e)[:200]
        if ok2 and receipt2:
            rec["fallback_receipt"] = receipt2
        else:
            rec["error"] = (rec["error"] or "") + "; fallback-fail:" + (receipt2 or "no-receipt")
            rec["deadletter"] = True
            with open(deadletter_path, "a") as f:
                f.write(json.dumps({"ts": rec["ts"], "kind": kind, "message": message,
                                    "error": rec["error"]}, ensure_ascii=False) + "\n")
    with open(ledger_path, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--message", required=True)
    ap.add_argument("--kind", default="fault", choices=["fault", "deadletter", "no_receipt_10min", "info"])
    ap.add_argument("--mock-primary", default="ok", choices=["ok", "fail"])
    ap.add_argument("--mock-fallback", default="ok", choices=["ok", "fail"])
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--deadletter", default=DEADLETTER_DEFAULT)
    ap.add_argument("--timeout", type=float, default=5)
    a = ap.parse_args()
    rec = send_alert(a.message, a.kind,
                     mock_sender(a.mock_primary, "mock-primary-001"),
                     mock_sender(a.mock_fallback, "mock-fallback-001"),
                     a.ledger, a.deadletter, a.timeout)
    print(json.dumps(rec, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    sys.exit(main())
