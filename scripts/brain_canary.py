#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import time
import urllib.request
from datetime import datetime

HUB = "/home/ubuntu/ai-team"
SCRIPTS = os.path.join(HUB, "scripts")
STATE = os.path.join(HUB, "state")
STATUS = os.path.join(STATE, "brain-status.json")
CONFIG = "/opt/openclaw-official/.openclaw/openclaw.json"


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def run(cmd, cwd=None, timeout=180):
    started = time.time()
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        # 2026-11-04 CANARYTIMEOUT: 单路探活超时不得中断整轮 full()，否则 brain-status.json
        # 会像 10-26→11-04 那样一直停在旧值，导致 BRAIN_DEGRADED 每 30 分钟重复刷 handoff。
        # 超时统一折算为 rc=124 的失败结果，让其余通道照常探测并落盘。
        stdout = e.stdout if isinstance(e.stdout, str) else ""
        stderr = e.stderr if isinstance(e.stderr, str) else ""
        p = subprocess.CompletedProcess(cmd, 124, stdout=stdout, stderr=stderr or "timeout")
    return p, round(time.time() - started, 1)


def opencode_text(model, marker, timeout=180):
    p, elapsed = run(
        ["opencode", "run", "--pure", "-m", model, "--format", "json",
         "--title", "brain-canary", "只回答四个字符：%s。不要解释，不要调用工具。" % marker],
        cwd="/tmp", timeout=timeout)
    texts = []
    for line in (p.stdout or "").splitlines():
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "text":
            texts.append((ev.get("part") or {}).get("text", ""))
    answer = " ".join(texts).strip()
    ok = p.returncode == 0 and marker in answer
    # M5-3-IMPORT: trace module loaded by absolute path (importlib); regular import is blind to it on this hub, see patch_trace_import.py
    # M5-3 trace (never breaks canary).
    try:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location("trace_20261105", "/home/ubuntu/ai-team/scripts/trace-20261105.py")
        _trace = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_trace)
        _trace.emit(task_id="braincanary-%s-%s-%s" % (model.replace("/", "-"), marker, datetime.now().strftime("%Y%m%d-%H%M%S")),
                    model_id=model, latency_ms=int(round(elapsed * 1000)),
                    source="brain-canary-opencode", returned_model=model if ok else "",
                    # M9-TRACE-VERIFY: content empty on failure so --verify recompute agrees.
                    timeout_s=timeout, content=answer if ok else "",
                    error="" if ok else "rc=%s answer=%s" % (p.returncode, answer[:120]),
                    timed_out=(p.returncode == 124), rc=p.returncode,
                    result_category="成功" if ok else ("超时" if p.returncode == 124 else "空"))
    except Exception:
        pass
    return {
        "status": "HEALTHY" if ok else "FAILED",
        "detail": "rc=%s marker=%s" % (p.returncode, marker in answer),
        "answer": answer[:200],
        "elapsed_s": elapsed,
    }


def zen_check():
    return opencode_text("opencode/deepseek-v4.1-flash-free", "ZEN_OK")


def fallback_check():
    return opencode_text("opencode/glm-5.3-flash-free", "FALLBACK_OK")


def web_brain_check(provider, marker, timeout=120):
    p, elapsed = run(
        ["sudo", "-n", "python3", "web_brain.py", "--provider", provider,
         "--prompt", "只回答四个字符：%s。不要解释。" % marker,
         "--timeout", str(timeout - 10)],
        cwd=SCRIPTS, timeout=timeout)
    data = {}
    for line in reversed((p.stdout or "").strip().splitlines()):
        try:
            data = json.loads(line)
            break
        except Exception:
            continue
    ok = data.get("status") == "ok" and marker in (data.get("answer") or "")
    # M5-3 trace (never breaks canary).
    try:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location("trace_20261105", "/home/ubuntu/ai-team/scripts/trace-20261105.py")
        _trace2 = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_trace2)
        _trace2.emit(task_id="braincanary-webbrain-%s-%s-%s" % (provider, marker, datetime.now().strftime("%Y%m%d-%H%M%S")),
                    model_id="web_brain/" + provider,
                    latency_ms=int(round(float(data.get("elapsed_s", elapsed)) * 1000)),
                    source="brain-canary-web-brain", returned_model="web_brain/" + provider,
                    timeout_s=timeout, content=(data.get("answer") or "") if ok else "",
                    error="" if ok else str(data.get("status") or "rc=%s" % p.returncode) + " answer=" + (data.get("answer") or "")[:120],
                    result_category="成功" if ok else "空")
    except Exception:
        pass
    return {
        "status": "HEALTHY" if ok else "FAILED",
        "detail": data.get("status") or ("rc=%s" % p.returncode),
        "answer": (data.get("answer") or "")[:200],
        "elapsed_s": data.get("elapsed_s", elapsed),
    }


def ds1_check():
    return web_brain_check("deepseek", "DS1_OK")


def expert_backup_check():
    return web_brain_check("claude", "CLAUDE_OK")


def gift_check():
    _t0 = time.monotonic(); _http = None; _err = ""; _exc = ""; _txt = ""
    try:
        key = open("/home/ubuntu/shared/worker-kit/newapi.key").read().strip()
        body = json.dumps({
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 8,
        }).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:3001/v1/chat/completions", data=body,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=40) as r:
            _http = r.status
            d = json.load(r)
        ct = (d.get("usage") or {}).get("completion_tokens", 0)
        _txt = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        _ok = bool(ct)
    except Exception as e:  # M9-GIFT-HTTP: capture the status code for the trace line.
        _http = getattr(e, "code", None)
        _err = "%s: %s" % (type(e).__name__, str(e)[:150])
        _exc = type(e).__name__
        _ok = False; ct = 0
    # M5-3 trace (never breaks canary).
    try:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location("trace_20261105", "/home/ubuntu/ai-team/scripts/trace-20261105.py")
        _trace3 = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_trace3)
        _trace3.emit(task_id="braincanary-gift-deepseek-chat-%s" % datetime.now().strftime("%Y%m%d-%H%M%S"),
                    model_id="deepseek-chat",
                    latency_ms=int(round((time.monotonic() - _t0) * 1000)),
                    source="brain-canary-gift", http_status=_http,
                    returned_model="deepseek-chat" if _ok else "",
                    timeout_s=40, content=_txt if _ok else "", error=_err,
                    timed_out=("timed out" in _err.lower()))  # M9-GIFT2: let classify() derive it from http_status
    except Exception:
        pass
    if _ok:
        return {"status": "HEALTHY", "detail": "completion_tokens=%s" % ct}
    return {"status": "DOWN", "detail": _exc or "DOWN"}


def load_status():
    try:
        with open(STATUS) as f:
            return json.load(f)
    except Exception:
        return {}


def save_status(data):
    data["updated_at"] = now()
    tmp = STATUS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATUS)


def set_lane(data, key, result, channel):
    old = data.get(key, {})
    item = dict(result)
    item["channel"] = channel
    item["last_checked"] = now()
    if item.get("status") == "HEALTHY":
        item["degraded_since"] = None
    else:
        item["degraded_since"] = old.get("degraded_since") or now()
    data[key] = item


def full():
    data = load_status()
    set_lane(data, "work_primary", zen_check(), "opencode/deepseek-v4.1-flash-free")
    set_lane(data, "work_backup", fallback_check(), "opencode/glm-5.3-flash-free")
    set_lane(data, "expert_primary", ds1_check(), "DeepSeek #1 web_brain")
    set_lane(data, "expert_backup", expert_backup_check(), "Claude web_brain")
    set_lane(data, "gift", gift_check(), "newapi channel 9 / ds2api")
    data["gift"]["alert"] = False
    data["policy"] = {
        "gift_alert": False,
        "keeper": "silent",
        "direct_chat_reasoner": "forbidden",
        "degraded_alert_after_s": 900,
    }
    save_status(data)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    out = os.path.join(STATE, "brain-canary-%s.json" % stamp)
    with open(out, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(json.dumps({"canary": out, "status": data}, ensure_ascii=False, indent=1))
    ok = data["work_primary"]["status"] == "HEALTHY" and (
        data["expert_primary"]["status"] == "HEALTHY" or
        data["expert_backup"]["status"] == "HEALTHY")
    return 0 if ok else 1


def failover_drill():
    bad, bad_s = run(
        ["opencode", "run", "--pure", "-m", "opencode/p0-3-no-such-model",
         "--format", "json", "只回答 OK"],
        cwd="/tmp", timeout=60)
    fb = fallback_check()
    result = {
        "time": now(),
        "primary_error": {"rc": bad.returncode, "elapsed_s": bad_s,
                          "stderr": (bad.stderr or "")[:200]},
        "fallback": fb,
        "verdict": "PASS" if bad.returncode != 0 and fb["status"] == "HEALTHY" else "FAIL",
    }
    out = os.path.join(STATE, "brain-failover-drill-%s.json" % datetime.now().strftime("%Y%m%d-%H%M"))
    with open(out, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(json.dumps({"drill": out, "result": result}, ensure_ascii=False, indent=1))
    return 0 if result["verdict"] == "PASS" else 1


def show():
    data = load_status()
    for k in ("work_primary", "work_backup", "expert_primary", "expert_backup", "gift"):
        v = data.get(k, {})
        print("%-15s %-8s %s degraded_since=%s" % (
            k, v.get("status"), v.get("channel"), v.get("degraded_since")))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--failover-drill", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.full:
        return full()
    if a.failover_drill:
        return failover_drill()
    return show()


if __name__ == "__main__":
    raise SystemExit(main())
