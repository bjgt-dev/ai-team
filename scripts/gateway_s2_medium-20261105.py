#!/usr/bin/env python3
"""S2 medium task matrix (2026-11-05, layers MEDIUM test pool).
3 medium tasks per model; each scored pass/fail + latency. No production use.
Tasks: (a) summarize 800-char doc to <=3 bullets containing the key number;
(b) extract 5-key JSON from a memo; (c) outline a 1500-char report (>=4 sections + figures).
Output: state/gateway-layers-20261105/s2-medium-20261105.json
"""
import json, re, time, urllib.request
OPENCODFIG = "/home/ubuntu/.config/opencode/opencode.json"
GATEWAY = "http://127.0.0.1:3001/v1"
OUT = "/home/ubuntu/ai-team/state/gateway-layers-20261105/s2-medium-20261105.json"

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
            _trace.emit(task_id="s2-%s-%s-%s" % (mid.replace("/", "-").replace(":", "-"), trace_label, time.strftime("%Y%m%d-%H%M%S")),
                        model_id=mid, latency_ms=int(round(elapsed * 1000)),
                        source="gateway-s2-medium", http_status=http_status,
                        returned_model=returned, timeout_s=timeout,
                        content=content_out, error=error,
                        timed_out=("timed out" in error.lower()),
                        expect_json=(trace_label == "b"))
    except Exception:
        pass
    return content_out, error, elapsed

DOC_A = ("季度运营简报：本季度新增注册用户 128400 人，环比增长 12.6%，其中通过自然搜索来访占 41%，"
         "社交渠道占 27%，其余为直接访问与推荐。付费转化率 3.8%，较上季度提升 0.4 个百分点，"
         "客单价 186 元。客服工单总量 5420 件，首次响应中位数 38 分钟，解决率 91.2%。"
         "服务器月均可用性 99.92%，P95 延迟 412 毫秒。下季度重点：把付费转化率从 3.8% 做到 4.2%，"
         "同时把 P95 延迟压到 350 毫秒以内，客服解决率目标 93%。")
MEMO_B = "会议纪要：2026-11-05 参加人张伟、李娜、王强。决定：官网改版 11 月 20 日上线，负责人李娜，预算 8 万元；客服排班改为三班倒，11 月 10 日起执行；下周三前提交 Q4 招聘计划，缺口 5 人。"
REPORT_C = ("年度复盘报告：今年公司营收 4820 万元，同比增长 23%。其中订阅收入 3010 万元，占比 62.5%，"
            "广告收入 1180 万元，占比 24.5%，其余为服务收入。用户规模年底达到 86 万，月活 41 万，"
            "留存率 68%。成本方面：人力成本 1950 万元，服务器与带宽 620 万元，市场费用 540 万元，"
            "合计 3110 万元，净利润 1710 万元。问题有三：第一，新用户获取成本从 42 元涨到 57 元；"
            "第二，大客户续约率 81%，低于 90% 的目标；第三，移动端崩溃率 0.8%，高于 0.5% 的红线。"
            "明年计划：获客成本压回 48 元以内，续约率做到 88%，崩溃率降到 0.4%，营收目标 6000 万元。")

def main():
    models = ["gemini-3-flash-preview", "gemini-3.5-flash", "gemini-3.6-flash"]
    results = {}
    for mid in models:
        time.sleep(3)
        r = {}
        c1, e1, l1 = call(mid, "把下面这段简报总结成不超过3条要点，每条不超过40字，必须包含关键数字 128400 和 3.8%：\n" + DOC_A, 300, 60, trace_label="a")
        r["s2a_summary"] = {"pass": bool(c1.strip()) and "128400" in c1 and "3.8%" in c1 and l1 <= 60,
                            "lat_s": round(l1, 2), "error": e1, "out_len": len(c1)}
        time.sleep(3)
        c2, e2, l2 = call(mid, "只输出JSON，不要其他文字，含5个键：date、attendees、decision1、owner、budget：\n" + MEMO_B, 300, 60, trace_label="b")
        try:
            j2 = json.loads(c2.strip().strip("`").replace("json", "", 1) if c2.strip().startswith("```") else c2.strip())
            ok2 = isinstance(j2, dict) and {"date", "attendees", "decision1", "owner", "budget"} <= set(j2.keys())
        except Exception:
            ok2 = False
        r["s2b_extract_json"] = {"pass": bool(ok2) and l2 <= 60, "lat_s": round(l2, 2), "error": e2, "out_len": len(c2)}
        time.sleep(3)
        c3, e3, l3 = call(mid, "把下面这份年度复盘写成提纲：至少4个一级标题，必须包含营收 4820 万元、净利润 1710 万元、明年营收目标 6000 万元这三个数字：\n" + REPORT_C, 500, 90, trace_label="c")
        r["s2c_outline"] = {"pass": bool(c3.strip()) and "4820" in c3 and "1710" in c3 and "6000" in c3 and l3 <= 90,
                            "lat_s": round(l3, 2), "error": e3, "out_len": len(c3)}
        r["score"] = "%d/3" % sum(1 for v in r.values() if isinstance(v, dict) and v["pass"])
        results[mid] = r
        print(json.dumps({mid: r}, ensure_ascii=False), flush=True)
    out = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "matrix": "S2-medium", "results": results}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("WROTE", OUT)

if __name__ == "__main__":
    main()
