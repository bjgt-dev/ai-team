#!/usr/bin/env python3
"""COMPLEX code-substitute test (2026-11-05, layers COMPLEX fallback pool).
Ask each fallback model to write a Python function + pytest, run pytest locally.
If the code does not run, the capability label (code/reasoning) is void -> back to cooling.
Output: state/gateway-layers-20261105/complex-code-20261105.json
"""
import json, os, re, subprocess, time, urllib.request
OPENCODFIG = "/home/ubuntu/.config/opencode/opencode.json"
GATEWAY = "http://127.0.0.1:3001/v1"
OUT = "/home/ubuntu/ai-team/state/gateway-layers-20261105/complex-code-20261105.json"
WORK = "/tmp/gateway-complex-20261105"

def token():
    c = json.load(open(OPENCODFIG))
    for p in c.get("provider", {}).values():
        opt = p.get("options") or {}
        if opt.get("apiKey"):
            return opt["apiKey"]
    return ""

def call(mid, content, max_tokens, timeout, trace_label=None):
    t0 = time.time()
    body = json.dumps({"model": mid, "messages": [{"role": "user", "content": content}],
                       "max_tokens": max_tokens, "temperature": 0}).encode()
    req = urllib.request.Request(GATEWAY + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json"})
    http_status = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            http_status = r.status
            d = json.loads(r.read().decode() or "{}")
        ch = (d.get("choices") or [])
        content_out = (ch[0].get("message") or {}).get("content") or ""
        returned = d.get("model", "")
        error = ""
    except Exception as e:
        http_status = getattr(e, "code", None)
        content_out, returned, error = "", "", str(e)[:200]
    elapsed = time.time() - t0
    # M9-TRACE-P2: one line per model call (never breaks eval).
    try:
        if trace_label:
            import sys as _sys
            _sys.path.insert(0, "/home/ubuntu/ai-team/scripts")
            import trace_20261105 as _trace
            _trace.emit(task_id="complex-%s-%s-%s" % (mid.replace("/", "-").replace(":", "-"), trace_label, time.strftime("%Y%m%d-%H%M%S")),
                        model_id=mid, latency_ms=int(round(elapsed * 1000)),
                        source="gateway-complex-code", http_status=http_status,
                        returned_model=returned, timeout_s=timeout,
                        content=content_out, error=error,
                        timed_out=("timed out" in error.lower()))
    except Exception:
        pass
    return content_out, error, elapsed

PROMPT = ("写一个 Python 模块：函数 add(a, b) 返回两数之和，函数 mul(a, b) 返回两数之积。"
          "再写 pytest 测试文件，覆盖 add(2,3)==5、add(-1,1)==0、mul(3,4)==12。"
          "只输出两个代码块，第一个是 solution.py，第二个是 test_solution.py，不要其他文字。")

def run_code(mid, text):
    os.makedirs(WORK, exist_ok=True)
    tag = re.sub(r"[^A-Za-z0-9]+", "_", mid)[:40]
    d = os.path.join(WORK, tag)
    os.makedirs(d, exist_ok=True)
    blocks = re.findall(r"```(?:python)?\s*(.*?)```", text, re.S)
    if len(blocks) < 2:
        return {"code_ran": False, "reason": "fewer than 2 code blocks: %d" % len(blocks)}
    open(os.path.join(d, "solution.py"), "w").write(blocks[0])
    open(os.path.join(d, "test_solution.py"), "w").write(blocks[1])
    p = subprocess.run(["python3", "-m", "pytest", "-q", "test_solution.py"],
                       cwd=d, capture_output=True, text=True, timeout=60)
    tail = (p.stdout + p.stderr)[-500:]
    return {"code_ran": p.returncode == 0, "pytest_rc": p.returncode, "pytest_tail": tail}

def main():
    results = {}
    for mid in ["nvidia/nemotron-3-ultra-550b-a55b:free", "llama-4-scout"]:
        time.sleep(3)
        txt, err, lat = call(mid, PROMPT, 800, 120, trace_label="code")
        r = {"lat_s": round(lat, 2), "error": err, "out_len": len(txt)}
        r.update(run_code(mid, txt) if txt else {"code_ran": False, "reason": "empty reply: " + err})
        results[mid] = r
        print(json.dumps({mid: r}, ensure_ascii=False), flush=True)
    _ran = sum(1 for r in results.values() if r.get("code_ran"))
    out = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "task": "COMPLEX code substitute",
           "summary": {"code_ran": "%d/%d" % (_ran, len(results))}, "results": results}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("WROTE", OUT)

if __name__ == "__main__":
    main()
