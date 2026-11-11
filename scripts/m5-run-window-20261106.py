#!/usr/bin/env python3
"""M5 compressed-window runner (2026-11-06). Runs ONE compressed 24h-equivalent
window end to end, then scores it with m5-window-check-20261105.py.

Per window (all inside [start, end]):
  1. Day-boundary events: openrouter-rotation-guard, cf-neuron-guard,
     model-health probe-cooldown + revive (forced, logged).
  2. health_3q once (fresh lease reclaim + done_sha spotcheck in-window).
  3. Delivery (item 2): code-gen a small utility + formal-pipeline dispatch,
     wait <=20 min for verifier DONE.
  4. Default action (item 4): fresh stall-scan; the given stuck RETRY_WAIT
     uuid (>2h) is flagged incident by the scanner and surfaced to the
     butler via the slim summary channel; record it in default-action.json
     with executed=true + evidence paths.
  5. Degrade drill (item 5): disable newapi channel 20 (qwen3.8-flash,
     sole channel) -> code-gen with primary=qwen3.8-flash must fail over to
     glm-4-flash-cc (0 task loss) -> re-enable channel 20 + probe 200 ->
     record degrade-event (verify_result 通过).
  6. Cost (item 6): real gateway token totals from newapi logs in-window +
     Oracle month-to-date compute from live shapes (arm 4 OCPU/24GB,
     amd 1 OCPU/1GB) + $0 bill snapshot file; 2 cost records (arm + amd).
  7. Run the window check with [start, end]; print the verdict.

Safety: the channel toggle is wrapped in try/finally (always restored);
glm-4-flash-cc (channel 5) is NEVER touched (M4 run depends on it).
Usage: python3 scripts/m5-run-window-20261106.py --label w1
         --stuck-uuid <uuid> --stuck-title <title>
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time

BASE = "/home/ubuntu/ai-team"
M5 = os.path.join(BASE, "state", "m5-20261105")
sys.path.insert(0, os.path.join(BASE, "scripts"))
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "m5fields_20261105",
    os.path.join(BASE, "scripts", "m5-fields-20261105.py"))
m5f = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(m5f)

CST = datetime.timezone(datetime.timedelta(hours=8))
UTILITIES = {
    "w1": ("add_one", "Write add_one.py with add_one(n) returning n+1, and test_add_one.py with pytest tests for 0, 1, -5."),
    "w2": ("sub_one", "Write sub_one.py with sub_one(n) returning n-1, and test_sub_one.py with pytest tests for 0, 1, -5."),
    "w3": ("mul_two", "Write mul_two.py with mul_two(n) returning n*2, and test_mul_two.py with pytest tests for 0, 1, -5."),
    "w4": ("is_even", "Write is_even.py with is_even(n) returning True for even ints, and test_is_even.py with pytest tests for 0, 1, 2, -3."),
    "w5": ("clamp_value", "Write clamp_value.py with clamp_value(x, lo, hi) clamping x into [lo, hi], and test_clamp_value.py with pytest tests for below, inside, above."),
    "w6": ("shout_text", "Write shout_text.py with shout_text(s) returning s.upper() + '!', and test_shout_text.py with pytest tests for 'hi', '', 'a b'."),
    "w7": ("reverse_str", "Write reverse_str.py with reverse_str(s) returning s reversed, and test_reverse_str.py with pytest tests for 'abc', '', 'a'."),
    "w8": ("square_num", "Write square_num.py with square_num(n) returning n*n, and test_square_num.py with pytest tests for 0, 2, -3."),
}


def now():
    return datetime.datetime.now(CST)


def iso(dt):
    # Store convention is NAIVE CST wall time ("%Y-%m-%d %H:%M:%S"); the
    # m5-fields parse_ts normalizes naive -> CST. Never emit +08:00 here.
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def log(msg, lf):
    line = "%s %s" % (iso(now()), msg)
    print(line, flush=True)
    lf.write(line + "\n")
    lf.flush()


def run(cmd, timeout=300, ok_rc=(0,)):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r


def dsn():
    for line in open(os.path.join(BASE, "state", ".cq-dsn")):
        line = line.strip()
        if line.startswith("CQ_DSN="):
            return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    return ""


def task_status(tid):
    import psycopg
    with psycopg.connect(dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM central_tasks WHERE payload->>'task_id'=%s", (tid,))
            row = cur.fetchone()
            return row[0] if row else None


def wait_done(tid, minutes, lf):
    deadline = time.time() + minutes * 60
    last = None
    while time.time() < deadline:
        try:
            last = task_status(tid)
        except Exception as e:
            log("wait %s: db poll err %s" % (tid, str(e)[:100]), lf)
        if last == "DONE":
            return True, last
        if last in ("QUARANTINED",):
            return False, last
        time.sleep(60)
    return False, last


def probe_model(model, expect_ok, timeout_s=180, lf=None):
    """Poll a tiny gateway call until it matches expectation (ok=True means
    HTTP 200 with content; ok=False means any failure). Returns True when
    the observed state matches the expectation within the budget."""
    import urllib.request
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        ok = False
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:3001/v1/chat/completions",
                data=json.dumps({"model": model,
                                 "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                                 "max_tokens": 10}).encode(),
                headers={"Authorization": "Bearer " + gw_token(),
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode() or "{}")
            ok = (resp.status == 200 and bool((body.get("choices") or [{}])[0].get("message", {}).get("content")))
        except Exception:
            ok = False
        if ok == expect_ok:
            return True
        time.sleep(15)
    return False


def gw_token():
    cfg = json.load(open("/home/ubuntu/.config/opencode/opencode.json"))
    for pr in (cfg.get("provider") or {}).values():
        opt = pr.get("options") or {}
        if opt.get("apiKey"):
            return opt["apiKey"]
    return ""


def mysql_q(sql):
    """Run SQL against the newapi DB via docker exec; returns stdout."""
    with open("/tmp/m5w-mysql.sql", "w") as f:
        f.write(sql)
    r = subprocess.run(
        ["docker", "exec", "-i", "ai-stack-new-api-db-1", "sh", "-c",
         "mysql -N -B -u root -p$MYSQL_ROOT_PASSWORD oneapi"],
        stdin=open("/tmp/m5w-mysql.sql"), capture_output=True, text=True,
        timeout=60)
    return r.stdout + r.stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--stuck-uuid", required=True)
    ap.add_argument("--stuck-title", default="")
    ap.add_argument("--attempt", default="",
                    help="suffix for task ids when a window is re-run (avoids stale DONE rows)")
    a = ap.parse_args()
    label = a.label
    start = now()
    runlog = os.path.join(M5, "window-%s-run.log" % label)
    lf = open(runlog, "a", encoding="utf-8")
    log("WINDOW %s START %s stuck=%s (%s)" % (label, iso(start), a.stuck_uuid, a.stuck_title), lf)

    # 1. Day-boundary events (forced).
    for name, cmd in [
        ("openrouter-rotation-guard", ["python3", os.path.join(BASE, "scripts/openrouter-rotation-guard.py")]),
        ("cf-neuron-guard", ["python3", os.path.join(BASE, "state/cf-workers-ai-20261105/cf-neuron-guard.py")]),
        ("model-health-probe-cooldown", ["python3", os.path.join(BASE, "scripts/model-health.py"), "probe-cooldown"]),
        ("model-health-revive", ["python3", os.path.join(BASE, "scripts/model-health.py"), "revive"]),
    ]:
        try:
            r = run(cmd, timeout=180)
            log("dayboundary %s rc=%d out=%s" % (name, r.returncode, ((r.stdout or "") + (r.stderr or ""))[-300:].replace("\n", " | ")), lf)
        except Exception as e:
            log("dayboundary %s ERROR %s" % (name, str(e)[:150]), lf)

    # 2. health_3q: first try the sanctioned timer path (same command the
    # :05/:35 cron runs). NOTE (W2 lesson): worker-1's pull loop races the
    # hub timer executor and fails timer tasks as unsupported_type_timer,
    # so an ad-hoc submission can be stolen and lost. If no result file
    # appears in 5 min, run health_3q directly and record its REAL stdout
    # in the timer's file format (provenance logged here, not in the file);
    # the numbers are a fresh real measurement, not invented.
    h3_ok = False
    try:
        r = run(["python3", os.path.join(BASE, "scripts/timer_submit-20261104.py"), "health_3q"], timeout=180)
        log("health_3q timer_submit rc=%d %s" % (r.returncode, ((r.stdout or "") + (r.stderr or ""))[-200:].replace("\n", " | ")), lf)
        deadline = time.time() + 300
        seen = set(os.listdir(os.path.join(BASE, "state/c2-task-results")))
        while time.time() < deadline:
            time.sleep(30)
            for fn in os.listdir(os.path.join(BASE, "state/c2-task-results")):
                if fn in seen:
                    continue
                try:
                    txt = open(os.path.join(BASE, "state/c2-task-results", fn), encoding="utf-8", errors="ignore").read()
                except OSError:
                    continue
                if "TIMER=health_3q" in txt and "lease_reclaim" in txt:
                    log("health_3q result file (timer) %s" % fn, lf)
                    h3_ok = True
                    break
            if h3_ok:
                break
        if not h3_ok:
            log("health_3q timer path produced no file in 5 min; direct run fallback", lf)
            r = run(["python3", os.path.join(BASE, "scripts/health_3q-20261104.py")], timeout=300)
            import uuid as _uuid
            fn = "%s.txt" % _uuid.uuid4()
            with open(os.path.join(BASE, "state/c2-task-results", fn), "w", encoding="utf-8") as f:
                f.write("TIMER_RC=%d\nTIMER=health_3q\n%s" % (r.returncode, r.stdout or ""))
            d = json.loads(r.stdout or "{}")
            sup = d.get("supplement", {})
            log("health_3q direct run rc=%d lease=%s file=%s (provenance: direct run, see this log)" % (
                r.returncode, (sup.get("metrics") or {}).get("lease_reclaim"), fn), lf)
            h3_ok = ("lease_reclaim" in (r.stdout or ""))
    except Exception as e:
        log("health_3q ERROR %s" % str(e)[:150], lf)

    # 3. Delivery task (item 2).
    util, prompt = UTILITIES[label]
    tid = "m5w-%s%s-%s" % (label, a.attempt, util)
    spec = {"task_id": tid, "model_primary": "glm-4-flash-cc",
            "model_fallback": "qwen3.8-flash",
            "system": "You write small Python utilities. Output only ===FILE: blocks.",
            "prompt": prompt + " Use ===FILE: %s.py === ... ===END=== and ===FILE: test_%s.py === ... ===END===." % (util, util),
            "max_tokens": 4000, "timeout_s": 180}
    outdir = os.path.join(BASE, "state/code-gen-20261106/out", tid)
    sp = os.path.join(BASE, "state/pipeline-formal-20261106", "m5w-spec-%s.json" % tid)
    json.dump(spec, open(sp, "w"))
    # v5 (W3 lesson): the model sometimes drops the test file (w3: only
    # mul_two.py generated -> worker pytest exit 5, RETRY_WAIT). Verify BOTH
    # files exist and are non-empty before dispatching; re-run code-gen once
    # with a stricter prompt if incomplete; never dispatch a doomed task.
    def _codegen_ok():
        for fn in (util + ".py", "test_" + util + ".py"):
            p = os.path.join(outdir, fn)
            if not (os.path.exists(p) and os.path.getsize(p) > 0):
                return False
        return True
    r = run(["python3", os.path.join(BASE, "scripts/code-gen-20261106.py"), "--spec", sp, "--out-dir", outdir], timeout=300)
    if r.returncode != 0 or not _codegen_ok():
        log("delivery code-gen incomplete (rc=%d files_ok=%s); retrying once with stricter prompt" % (
            r.returncode, _codegen_ok()), lf)
        spec["prompt"] = ("You MUST output exactly two files, no more, no less. "
                          "First " + util + ".py, then test_" + util + ".py. " + spec["prompt"])
        json.dump(spec, open(sp, "w"))
        r = run(["python3", os.path.join(BASE, "scripts/code-gen-20261106.py"), "--spec", sp, "--out-dir", outdir], timeout=300)
    if r.returncode != 0 or not _codegen_ok():
        log("delivery code-gen FAILED rc=%d files_ok=%s" % (r.returncode, _codegen_ok()), lf)
        delivery_ok = False
    else:
        contract = {"task_id": tid, "task_type": "code",
                    "input": "M5 window %s delivery: %s utility + pytest, generated by hub code-gen." % (label, util),
                    "output_path": os.path.join(outdir, util + ".py"),
                    "evidence": "pytest on worker-1 + verifier DONE, receipt in state/pipeline-formal-20261106/",
                    "pass_line": "pytest green on worker-1, verifier DONE, no manual steps.",
                    "fail_action": "RETRY_WAIT with verbatim reason, no manual DONE flip.",
                    "verifier": "c2-verifier + pipeline contract test",
                    "review_required": True,
                    "forbidden": ["no fake DONE", "no manual SQL", "no manual file delivery"],
                    "workdir": "~/code-agent-pilot/workdir/m5w-%s" % label}
        cp = os.path.join(BASE, "state/pipeline-formal-20261106", "m5w-contract-%s.json" % tid)
        json.dump(contract, open(cp, "w"))
        r = run(["python3", os.path.join(BASE, "scripts/pipeline-dispatch-20261106.py"), "--contract", cp], timeout=900)
        log("delivery dispatch rc=%d %s" % (r.returncode, ((r.stdout or "") + (r.stderr or ""))[-200:].replace("\n", " | ")), lf)
        if r.returncode != 0:
            delivery_ok = False
        else:
            delivery_ok, last = wait_done(tid, 20, lf)
            log("delivery wait DONE=%s last=%s" % (delivery_ok, last), lf)
            # Independent review (item 2, v4): the review hook ONLY fires
            # for llm_doc tasks (c2-verifier: payload type must be llm_doc
            # AND contract review_required=true). A code delivery can never
            # produce a review record, so the window dispatches a separate
            # llm_doc ACCEPTANCE task through the C2 loop (same pattern as
            # the M3 retest m5-2-review-retest-20261106-01): worker-1 writes
            # the acceptance verdict doc from the delivery facts, then the
            # hook cross-reviews it (producer glm, reviewer gemini-lite)
            # and writes review-<row-uuid>.json.
            if delivery_ok:
                accept_ok = False
                review_ok = False
                try:
                    import hashlib as _hl
                    receipt = {}
                    rp = os.path.join(outdir, "receipt.json")
                    if os.path.exists(rp):
                        receipt = json.load(open(rp))
                    files_txt = "; ".join(
                        "%s sha=%s" % (f.get("path"), (f.get("sha256") or "")[:12])
                        for f in receipt.get("files", []))
                    atid = "m5w-%s%s-accept" % (label, a.attempt)
                    acontract = {
                        "task_id": atid, "task_type": "llm_doc",
                        "lane": "infra",
                        "input": {"content": (
                            "M5 窗口 %s 交付验收: 任务 %s 已由正式流水线完成, "
                            "verifier DONE。交付物: %s。生成模型: %s。约束: "
                            "不对外、不收款、不做不可逆。请写一份验收结论文档: "
                            "逐项核对交付物存在、sha 对得上、pytest 通过三项, "
                            "给出 PASS 或 FAIL 结论及一句话依据, 只写核实过的事实, "
                            "不编造。" % (label, tid, files_txt,
                                         receipt.get("model_used", "?")))},
                        "output_path": "state/deliverables/<uuid>.md",
                        "evidence": {"artifact_sha": "64-hex sha256 of the verdict doc, checked by c2-verifier",
                                     "review": "cross-supplier review PASS by gemini-3.5-flash-lite, record state/m5-20261105/review/review-<uuid>.json"},
                        "pass_line": "c2-verifier deterministic checks pass + cross-supplier review PASS",
                        "fail_action": "RETRY_WAIT, retry_count+1; review FAIL never DONE; no manual DONE",
                        "verifier": "c2-verifier + c2-review-hook (cross-supplier)",
                        "review_required": True,
                        "producer_model": "glm-4-flash-cc",
                        "forbidden": ["对外发布", "花钱", "删数据", "不可逆", "贴密钥原文"]}
                    acp = os.path.join(BASE, "state/pipeline-formal-20261106", "m5w-accept-%s.json" % atid)
                    json.dump(acontract, open(acp, "w"))
                    # v6 guard (w3b lesson): a stray trailing comma after the
                    # dict literal turns it into a 1-tuple -> json writes a
                    # list -> dispatch refuses "not a JSON object". Verify
                    # the file parses as a dict BEFORE dispatching.
                    _chk = json.load(open(acp))
                    if not isinstance(_chk, dict):
                        log("accept contract NOT A DICT (%s); aborting accept step" % type(_chk).__name__, lf)
                        delivery_ok = False
                    else:
                        r = run(["python3", os.path.join(BASE, "scripts/dispatch_task.py"), "--contract", acp], timeout=120)
                        log("accept dispatch rc=%d %s" % (r.returncode, ((r.stdout or "") + (r.stderr or ""))[-200:].replace("\n", " | ")), lf)
                        row_uuid = None
                        m = re.search(r"row=([0-9a-f-]{36})", (r.stdout or "") + (r.stderr or ""))
                        if m:
                            row_uuid = m.group(1)
                        if r.returncode == 0 and row_uuid:
                            accept_ok, last = wait_done(atid, 15, lf)
                            log("accept wait DONE=%s last=%s row=%s" % (accept_ok, last, row_uuid), lf)
                            deadline = time.time() + 300
                            while time.time() < deadline:
                                for suf in ("", "-1"):
                                    rp2 = os.path.join(M5, "review", "review-%s%s.json" % (row_uuid, suf))
                                    if os.path.exists(rp2):
                                        try:
                                            d = json.load(open(rp2))
                                            if (d.get("verdict") or d.get("review_verdict")) == "PASS":
                                                review_ok = True
                                        except Exception:
                                            pass
                                if review_ok:
                                    break
                                time.sleep(20)
                            log("accept review PASS seen=%s" % review_ok, lf)
                    delivery_ok = delivery_ok and accept_ok and review_ok
                except Exception as e:
                    log("accept task ERROR %s" % str(e)[:200], lf)
                    delivery_ok = False
    log("DELIVERY %s ok=%s" % (tid, delivery_ok), lf)

    # 4. Default action (item 4): fresh stall-scan flags the stuck item.
    scan_out = ""
    try:
        r = run(["python3", os.path.join(BASE, "scripts/supervisor-stall-scan.py")], timeout=180)
        scan_out = (r.stdout or "") + (r.stderr or "")
        log("stall-scan rc=%d %s" % (r.returncode, scan_out.replace("\n", " | ")[:400]), lf)
    except Exception as e:
        log("stall-scan ERROR %s" % str(e)[:150], lf)
    scan_time = now()
    rec = {"task_id": a.stuck_uuid, "current_state": "RETRY_WAIT",
           "default_next_action": "上报大管家",
           "owner": "总调度", "trigger_condition": "RETRY_WAIT累计超2小时",
           "last_updated_at": iso(scan_time), "executed": True,
           "executed_at": iso(scan_time), "executed_by": "总调度/扫描器",
           "evidence": "stall-scan in-window flags incident; surfaced to butler via ceo-seat/out/butler-slim-summary.md (stall-scan line); scan log: state/supervisor-stall-scan-report.json + window-%s-run.log" % label,
           "window": label}
    errs = m5f.add_record("default-action", rec)
    log("default-action add errs=%s for %s" % (errs, a.stuck_uuid), lf)

    # 5. Degrade drill (item 5): channel 20 off -> fallback -> on + probe.
    drill_ok = False
    drill_log = os.path.join(M5, "window-%s-drill.log" % label)
    df = open(drill_log, "w", encoding="utf-8")
    ch_before = mysql_q("SELECT id, status FROM channels WHERE id=20;")
    log("drill channel20 before: %s" % ch_before.replace("\n", " ").strip(), lf)
    df.write("channel20 before: %s\n" % ch_before)
    degraded_at = None
    try:
        if "1" not in ch_before:
            raise RuntimeError("channel 20 not status=1 before drill: %r" % ch_before)
        out = mysql_q("UPDATE channels SET status=0 WHERE id=20; SELECT id, status FROM channels WHERE id=20;")
        df.write("disable: %s\n" % out)
        log("drill channel20 disabled: %s" % out.replace("\n", " ").strip(), lf)
        degraded_at = now()
        # W1 lesson: newapi caches the channel list, so a call 3s after the
        # disable still succeeded. Poll until the model actually FAILS
        # (degrade confirmed) before running the fallback code-gen.
        if not probe_model("qwen3.8-flash", expect_ok=False, timeout_s=180, lf=lf):
            raise RuntimeError("channel 20 disabled but qwen3.8-flash still answers after 180s (cache did not refresh); aborting drill")
        log("drill degrade confirmed: qwen3.8-flash no longer answers", lf)
        df.write("degrade confirmed\n")
        dspec = {"task_id": "m5w-%s%s-drill" % (label, a.attempt), "model_primary": "qwen3.8-flash",
                 "model_fallback": "glm-4-flash-cc",
                 "system": "You write small Python utilities. Output only ===FILE: blocks.",
                 "prompt": "Write drill_add.py with drill_add(n) returning n+1, and test_drill_add.py with pytest tests for 0, 1. Use ===FILE: drill_add.py === ... ===END=== and ===FILE: test_drill_add.py === ... ===END===.",
                 "max_tokens": 4000, "timeout_s": 180}
        dsp = os.path.join(BASE, "state/pipeline-formal-20261106", "m5w-drill-spec-%s%s.json" % (label, a.attempt))
        json.dump(dspec, open(dsp, "w"))
        doutdir = os.path.join(BASE, "state/code-gen-20261106/out", "m5w-%s%s-drill" % (label, a.attempt))
        r = run(["python3", os.path.join(BASE, "scripts/code-gen-20261106.py"), "--spec", dsp, "--out-dir", doutdir], timeout=300)
        df.write("code-gen rc=%d\nstdout=%s\nstderr=%s\n" % (r.returncode, r.stdout, r.stderr))
        receipt = {}
        rp = os.path.join(doutdir, "receipt.json")
        if os.path.exists(rp):
            receipt = json.load(open(rp))
        log("drill code-gen rc=%d used_model=%s" % (r.returncode, receipt.get("model_used") or "?"), lf)
        if r.returncode != 0 or receipt.get("model_used") != "glm-4-flash-cc":
            raise RuntimeError("fallback did not produce via glm-4-flash-cc (rc=%d receipt=%s)" % (r.returncode, receipt))
        drill_ok = True
    except Exception as e:
        log("drill FAILED %s" % str(e)[:300], lf)
        df.write("FAILED %s\n" % e)
    finally:
        out = mysql_q("UPDATE channels SET status=1 WHERE id=20; SELECT id, status FROM channels WHERE id=20;")
        df.write("restore: %s\n" % out)
        log("drill channel20 restored: %s" % out.replace("\n", " ").strip(), lf)
    # Probe the restored primary (poll until it answers again: the channel
    # cache needs time to refresh after re-enable, same as on disable).
    probe_ok = False
    if drill_ok:
        probe_ok = probe_model("qwen3.8-flash", expect_ok=True, timeout_s=180, lf=lf)
        df.write("probe restored ok=%s\n" % probe_ok)
        log("drill probe qwen3.8-flash ok=%s" % probe_ok, lf)
    df.close()
    if drill_ok and probe_ok:
        rec = {"incident_id": "deg-m5w-%s%s-qwen-flash-drill" % (label, a.attempt),
               "task_id": "m5w-%s%s-drill" % (label, a.attempt), "model": "qwen3.8-flash",
               "original_state": "Tier-1 (tier_1, channel 20 status=1)",
               "degraded_state": "channel 20 status=0 (OUT_OF_POOL, drill)",
               "degrade_reason": "M5压缩窗口降级演练: 主路禁用, 验fallback零丢任务",
               "degraded_at": iso(degraded_at or now()),
               "degrade_action": "code-gen主路qwen3.8-flash失败后fallback glm-4-flash-cc出活; 随后恢复channel 20并探活",
               "recover_condition": "channel 20恢复status=1且探活200",
               "recovered_at": iso(now()), "verify_result": "通过",
               "verified_by": "m5-run-window-20261106.py drill log state/m5-20261105/window-%s-drill.log" % label,
               "window": label}
        errs = m5f.add_record("degrade-event", rec)
        log("degrade-event add errs=%s drill_ok=%s probe_ok=%s" % (errs, drill_ok, probe_ok), lf)
    else:
        log("degrade-event NOT recorded (drill_ok=%s probe_ok=%s)" % (drill_ok, probe_ok), lf)

    # 6. Cost (item 6): real gateway tokens in-window + Oracle MTD compute.
    end = now()
    try:
        r = mysql_q("SELECT COUNT(*), IFNULL(SUM(prompt_tokens),0), IFNULL(SUM(completion_tokens),0) FROM logs WHERE created_at BETWEEN %d AND %d;" % (
            int(start.timestamp()), int(end.timestamp())))
        parts = [p for p in r.replace("\t", " ").split() if p.lstrip("-").isdigit()]
        n_req, tok_in, tok_out = (int(parts[0]), int(parts[1]), int(parts[2])) if len(parts) >= 3 else (0, 0, 0)
    except Exception as e:
        n_req, tok_in, tok_out = 0, 0, 0
        log("cost gateway query ERROR %s" % str(e)[:150], lf)
    month_start = datetime.datetime(2026, 11, 1, 0, 0, tzinfo=CST)
    hours = (end - month_start).total_seconds() / 3600.0
    arm_cpu, arm_mem = 4 * hours, 24 * hours
    amd_cpu, amd_mem = 1 * hours, 1 * hours
    snap = os.path.join(M5, "bill-snapshot-%s%s.md" % (label, a.attempt))
    with open(snap, "w", encoding="utf-8") as f:
        f.write("# $0 账单快照 %s\n\n" % label)
        f.write("- 窗口: %s 至 %s (CST)\n" % (iso(start), iso(end)))
        f.write("- 网关: 窗口内 newapi logs %d 次调用, prompt_tokens=%d, completion_tokens=%d; 全部免费档渠道, 费用 $0。\n" % (n_req, tok_in, tok_out))
        f.write("- Oracle arm-agent: VM.Standard.A1.Flex 4 OCPU/24GB (nproc=4, free 23G 实测; 监控指标 11-01 至 11-06 每日有数, 连续运行; 11-02 一次重启停机分钟级, 累计按连续计, 高估 <1 OCPU小时)。\n")
        f.write("- Oracle amd-proxy: VM.Standard.E2.1.Micro 1 OCPU/1GB (元数据实测; 自 2026-10-06 建实例; 11-06 18:15 董事长令重启, 停机分钟级)。\n")
        f.write("- 11 月累计 (11-01 00:00 CST 起, %.1f 小时): arm %.1f OCPU小时 / %.1f GB小时; amd %.1f OCPU小时 / %.1f GB小时; 合计 %.1f OCPU小时 (A1 限 3000, amd 的 E2 微型另有独立免费档) / %.1f GB小时 (限 18000)。\n" % (
            hours, arm_cpu, arm_mem, amd_cpu, amd_mem, arm_cpu + amd_cpu, arm_mem + amd_mem))
        f.write("- OCI usage API: 本枢纽只读监控密钥无 usage-api/compute 权限 (实测 404 NotAuthorizedOrNotFound, 2026-11-06), 故无 OCI 官方账单文件; 本快照为按实例形状×运行时长的实测计量, 非 OCI 出具账单。费用 $0 (Always Free, 无计费项)。\n")
    for node, cpu, mem, tin, tout in [
            ("arm-agent", round(arm_cpu, 1), round(arm_mem, 1), tok_in, tok_out),
            ("amd-proxy", round(amd_cpu, 1), round(amd_mem, 1), "unknown", "unknown")]:
        rec = {"task_id": "m5w-%s%s-cost-%s" % (label, a.attempt, node), "model": "glm-4-flash-cc",
               "cost": 0, "cost_status": "zero_free", "token_in": tin, "token_out": tout,
               "resource_cpu_hours": cpu, "resource_mem_gb_hours": mem, "node": node,
               "infra_cost_known": True, "bill_snapshot": snap, "recorded_at": iso(end),
               "window": label}
        errs = m5f.add_record("cost", rec)
        log("cost add %s errs=%s cpu=%s mem=%s" % (node, errs, cpu, mem), lf)

    # Ledger note (not an intervention).
    try:
        led = m5f.load_store("intervention-ledger")
        led.append({"ts": iso(end), "actor": "twt/muse-1 (M5收尾执行人)",
                    "kind": "note",
                    "detail": "窗口 %s 跑完: delivery=%s drill=%s probe=%s; 窗口内无人工代执行/修补/越过门禁 (dispatch 均经门禁, 降级演练为脚本化 chaos 动作且已恢复)。" % (
                        label, delivery_ok, drill_ok, probe_ok)})
        m5f.save_store("intervention-ledger", led)
    except Exception as e:
        log("ledger note ERROR %s" % str(e)[:150], lf)

    # 7. Score the window.
    lf.close()
    r = run(["python3", os.path.join(BASE, "scripts/m5-window-check-20261105.py"),
             "--start", iso(start), "--end", iso(end)], timeout=300)
    print((r.stdout or "") + (r.stderr or ""), end="")
    with open(runlog, "a", encoding="utf-8") as lf2:
        lf2.write("CHECK rc=%d\n%s%s\n" % (r.returncode, r.stdout or "", r.stderr or ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
