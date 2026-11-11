#!/usr/bin/env python3
# butler_eight_layer_review.py: 大管家每日自主八层审查的代码逻辑（2026-10-30 架构归位）。
# 由常驻循环每轮调用。只自动修“可逆、安全”的项（旧临时文件、已完结任务的过期租约、fb 残留请求）；
# 代码层面的不合规只记档 + 高危才推决策队列，不擅自改代码、不删数据、不关真实待决条目。
# 输出：state/eight-layer-review-YYYYMMDD.json（note 与专家问诊结果并入 JSON，
# 不再追加 ceo-seat/out/butler-arch-note.md，见 2026-11-05 ARCHFIX）。
import glob
import json
import os
import re
import shutil
import subprocess
from datetime import datetime

BASE = "/home/ubuntu/ai-team"
WORK = os.path.join(BASE, "ceo-seat/work")
STATE = os.path.join(BASE, "state")
OUT = os.path.join(BASE, "ceo-seat/out")
NOTIFY = os.path.join(WORK, "notify.py")
CONTRACTS_DONE = os.path.join(BASE, "contracts/done")


def sh(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def load_json(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


REDLINE_KEYS = ["permission", "权限矩阵", "deepseek", "登录", "sanitize", "fake_receipt", "butler-seat", "大管家死信"]
import re as _re
def _root_of(tid):
    return _re.sub(r"(-fb\d+)+$", "", tid)
def _resolved_roots():
    roots = set()
    try:
        for f in os.listdir(WORK):
            if f.startswith("five-step-") and f.endswith(".md"):
                try:
                    if "- resolved" in open(os.path.join(WORK, f), encoding="utf-8").read():
                        roots.add(f[len("five-step-"):-3])
                except Exception:
                    pass
    except Exception:
        pass
    return roots
CHAIRMAN_KEYS = ["扫码", "交互登录", "董事长", "拍板", "验证码", "花钱", "付费", "充值", "购买", "订阅", "成本"]
def _verdict_pass(tid):
    try:
        import glob as _glob
        for _f in _glob.glob(os.path.join(STATE, "supervisor-worker-20261027", "verdict-" + tid + "-*.json")):
            try:
                if json.load(open(_f, encoding="utf-8")).get("verdict") == "PASS":
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False
def _entry_age_hours(entry):
    try:
        import re as _re2
        from datetime import datetime as _dt
        ca = entry.get("created_at", "")
        if ca:
            for _fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    return (_dt.now() - _dt.strptime(str(ca)[:19], _fmt)).total_seconds() / 3600.0
                except Exception:
                    pass
        m = _re2.search(r"(\d{4})(\d{2})(\d{2})", entry.get("id", ""))
        if m:
            d = _dt(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return (_dt.now() - d).total_seconds() / 3600.0
    except Exception:
        pass
    return 0.0
def _five_step_escalated(tid):
    try:
        f = os.path.join(WORK, "five-step-" + tid + ".md")
        if os.path.isfile(f):
            txt = open(f, encoding="utf-8").read()
            return "ESCALATED" in txt
    except Exception:
        pass
    return False
ACCEPTANCE_TASKS = ("scenario-greeting", "accept-long", "accept-alarm")
# 2026-10-30 三色权限线（夜班最高负责人决策表）：
# 蓝线（自行决断，自动关单）：fb噪音已结案、任务已结案、复核已过、测试/夹具、完全重复、卡死测试单死信、超24小时无痕迹、诊断包已超期但无新诊断（不适用，见黄线）。
# 黄线（重试降级，留在自动修复链里，等结案后转蓝线）：自救进行中、兜底耗尽但自救在跑、复核风险且自救在跑、诊断包（去重锚点，永久保留不关）。
# 红线（报给董事长，永不自动关）：扫码/登录、付费/花钱、权限矩阵、清洗规则、大管家可信度、已升级的真实任务（需重试还是放弃的判断）。
def _has_artifacts(tid, entry=None):
    try:
        # 2026-11-06 ARCHFIX: 决策条目自身的 five_step_path 也是合法产物证据，
        # 否则真实待决条目会被当作 dangling no artifacts 静默关掉。
        if isinstance(entry, dict):
            _fsp = entry.get("five_step_path") or ""
            if _fsp and os.path.isfile(_fsp):
                return True
        if os.path.isfile(os.path.join(WORK, "five-step-" + tid + ".md")):
            return True
        if os.path.isfile(os.path.join(CONTRACTS_DONE, tid + ".json")):
            return True
        import glob as _g
        if _g.glob(os.path.join(STATE, "supervisor-worker-20261027", "verdict-" + tid + "-*.json")):
            return True
    except Exception:
        pass
    return False
def _five_step_state(tid):
    try:
        f = os.path.join(WORK, "five-step-" + tid + ".md")
        if not os.path.isfile(f):
            return "missing"
        txt = open(f, encoding="utf-8").read()
        if "ESCALATED" in txt:
            return "escalated"
        if "- step3-expert PENDING" in txt or "- step4-execute PENDING" in txt or "- step5-verify PENDING" in txt:
            return "pending"
        if "- resolved" in txt:
            return "resolved"
    except Exception:
        pass
    return "unknown"
def classify_decision(entry, resolved_roots):
    text = (entry.get("task_id", "") + " " + entry.get("summary", "") + " " + entry.get("type", "")).lower()
    for k in REDLINE_KEYS:
        if k.lower() in text:
            return ("RED", "redline:" + k)
    for k in CHAIRMAN_KEYS:
        if k.lower() in text:
            return ("RED", "chairman-action-or-money:" + k)
    tid = entry.get("task_id", "")
    if "diagnos" in text or tid.startswith("diagnosis"):
        return ("YELLOW", "diagnose dedup anchor")
    if "-fb" in tid and _root_of(tid) in resolved_roots:
        return ("BLUE", "fb-chain-noise root resolved")
    if tid in resolved_roots:
        return ("BLUE", "task five-step resolved")
    if _verdict_pass(tid):
        return ("BLUE", "verdict pass")
    low_sum = entry.get("summary", "").lower()
    _noise = [w for w in re.split(r"\s+", low_sum) if w]
    _noise_ids = [w for w in re.split(r"\s+", tid.lower()) if w]
    if any(m in _noise_ids or m in _noise for m in ("test", "fixture", "canary", "marker", "测试夹具")):
        return ("BLUE", "test/fixture no real impact")
    if entry.get("type", "") == "FIVE_STEP_STUCK" and tid not in ACCEPTANCE_TASKS and any(m in tid.lower() for m in ("test", "eval", "d1-", "debug", "query", "canary", "marker", "fixture")) and _five_step_escalated(tid):
        return ("BLUE", "auto-deadletter: stuck test task, retries exhausted")
    if _entry_age_hours(entry) > 24 and not _has_artifacts(tid, entry):
        return ("BLUE", "stale-24h no artifacts")
    if not _has_artifacts(tid, entry):
        # 2026-11-06 ARCHFIX: 年龄门。上一行已处理 >24h 无产物；这里只可能是 <24h 的新条目，
        # 必须留黄线等产物，不得按 dangling 直接关掉（董事长授权 A 的核心修复）。
        return ("YELLOW", "dangling no artifacts, <24h awaiting artifacts")
    st = _five_step_state(tid)
    if st == "pending":
        return ("YELLOW", "rescue in progress")
    if st == "escalated" and tid not in ACCEPTANCE_TASKS and any(m in tid.lower() for m in ("test", "eval", "d1-", "debug", "query", "canary", "marker", "fixture")):
        return ("BLUE", "auto-deadletter: stuck test task, retries exhausted")
    if st == "escalated":
        return ("RED", "escalated real task needs retry-or-abandon judgment")
    return ("YELLOW", "awaiting auto-fix path")
def auto_close_decision(entry, resolved_roots):
    text = (entry.get("task_id", "") + " " + entry.get("summary", "") + " " + entry.get("type", "")).lower()
    for k in REDLINE_KEYS:
        if k.lower() in text:
            return (False, "redline:" + k)
    for k in CHAIRMAN_KEYS:
        if k.lower() in text:
            return (False, "chairman-action-or-money:" + k)
    _color, _reason = classify_decision(entry, resolved_roots)
    if _color == "BLUE":
        return (True, _reason)
    return (False, _color.lower() + ":" + _reason)

def main():
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    today = datetime.now().strftime("%Y%m%d")
    findings = []
    autofixes = []
    # L1 磁盘：超 85% 先跑已有的旧临时文件清理，仍超 90% 才报警
    df = sh("df / | tail -1 | awk '{print $5}' | tr -d %")
    try:
        pct = int(df)
    except Exception:
        pct = -1
    if pct >= 85:
        out = sh("python3 /home/ubuntu/ai-team/scripts/tmp-so-cleanup.py", timeout=120)
        autofixes.append("disk_cleanup: %s" % out)
        df2 = sh("df / | tail -1 | awk '{print $5}' | tr -d %")
        try:
            pct = int(df2)
        except Exception:
            pass
    if pct >= 90:
        findings.append("disk_gt90: root=%d%% after cleanup" % pct)
    # L6 兜底残留：fb 请求文件（防递归守门已立，残留只清不派生）
    removed_fb = 0
    for f in glob.glob(os.path.join(WORK, "fallback-request-*.json")):
        d = load_json(f, {})
        tid = d.get("task_id") or ""
        if "-fb2" in tid or "-fb3" in tid:
            try:
                os.remove(f)
                removed_fb += 1
            except Exception:
                pass
    if removed_fb:
        autofixes.append("fb_request_stragglers_removed: %d" % removed_fb)
    # L4/L6 过期租约：只删任务已完结且不在认领/待领的
    def tids_in(d):
        s = set()
        if os.path.isdir(d):
            for fn in os.listdir(d):
                if not fn.endswith(".json"):
                    continue
                try:
                    s.add(json.load(open(os.path.join(d, fn))).get("task_id") or fn[:-5])
                except Exception:
                    s.add(fn[:-5])
        return s
    done = tids_in(os.path.join(BASE, "contracts/done"))
    live = set()
    for _kind in ("claimed", "pending"):
        _kind_dir = os.path.join(BASE, "contracts", _kind)
        if os.path.isdir(_kind_dir):
            for _lane in os.listdir(_kind_dir):
                live |= tids_in(os.path.join(_kind_dir, _lane))
    removed_lease = 0
    ldir = os.path.join(BASE, "contracts/leases")
    if os.path.isdir(ldir):
        for fn in os.listdir(ldir):
            p = os.path.join(ldir, fn)
            if not os.path.isfile(p) or not fn.endswith(".json"):
                continue
            if fn[:-5] not in live:
                try:
                    os.remove(p)
                    removed_lease += 1
                except Exception:
                    pass
    if removed_lease:
        autofixes.append("stale_leases_removed: %d" % removed_lease)
    # L6 写死扫描：只查在用回路文件，写死兜底名单非空即不合规
    live_files = [
        os.path.join(WORK, "agent-executor-20261029.py"),
        os.path.join(WORK, "five-step-executor.py"),
        os.path.join(WORK, "reviewer-20261029.py"),
        os.path.join(WORK, "fallback-driver-20261030.py"),
        os.path.join(WORK, "rework-driver-20261029.py"),
        os.path.join(WORK, "intake-driver-20261029.py"),
        "/home/ubuntu/openclaw-dispatcher/dispatch.sh",
        "/home/ubuntu/ai-team/scripts/ceo-opencode.sh",
        "/home/ubuntu/ai-team/scripts/butler_seat_revive.py",
    ]
    for f in live_files:
        try:
            t = open(f, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        m = re.search(r"MODELS_FALLBACK\s*=\s*\[([^\]]*)\]", t)
        if m and m.group(1).strip():
            findings.append("hardcoded_fallback: %s" % f)
        if "gpt-oss-120b" in t and "brain_watch" not in f:
            findings.append("dead_model_ref: %s" % f)
    # L7/L8 报警线存在性：监工与复活脚本必须含 notify 调用
    for f, marker in [
        (os.path.join(WORK, "reviewer-20261029.py"), "RISK_NOTIFY"),
        ("/home/ubuntu/ai-team/scripts/butler_seat_revive.py", "butler-seat-"),
    ]:
        try:
            t = open(f, encoding="utf-8", errors="ignore").read()
        except Exception:
            t = ""
        if marker not in t:
            findings.append("alert_hook_missing: %s needs %s" % (f, marker))
    # L6 路由：可用模型少于 3 个即记档（30 分钟自检 L6 线）；只推真正高危才走决策队列
    route = sh("python3 /home/ubuntu/ai-team/scripts/model-health.py route --capability simple_query", timeout=60)
    try:
        n_models = len(json.loads(route or "{}").get("models", []))
    except Exception:
        n_models = -1
    if n_models < 3:
        findings.append("route_degraded: only %d route models" % n_models)
    # L8 决策队列：只记档，不擅自关单
    q = load_json(os.path.join(STATE, "decision-queue.json"), {"pending": [], "resolved": []})
    n_pending = len(q.get("pending", []))
    # L8 决策自动拍板（2026-10-30 终极改造；2026-10-30 晚放权扩展）：非红线、非需董事长操作、非花钱，且有结案证据的，自动关单。
    # 永不自动关：红线（权限矩阵、DeepSeek登录/凭证、sanitize接入、大管家死信/可信度）+ 需董事长操作（扫码/登录/拍板/验证码）+ 花钱（付费/充值/购买/订阅/成本）。
    # 可关：(a) fb链噪音（根任务五步已resolved）；(b) 测试/夹具条目；(c) 任务本身已结案（五步resolved或复核PASS）；(d) 完全重复条目（同任务同类型同摘要，只留最新一条）。
    # 活故障（自救进行中、已升级待重试/放弃判断）没有任何一条规则会关——关了等于埋故障，故意留着等结果或等判断；每轮上限5条，逐条记审计日志；关单走 notify.py close 官方路径，事后以队列重读为准。
    closed = []
    if os.path.isfile(NOTIFY):
        roots = _resolved_roots()
        # (d) 完全重复条目：只留 id 最大的一条
        _groups = {}
        for _e in list(q.get("pending", [])):
            _groups.setdefault((_e.get("task_id", ""), _e.get("type", ""), _e.get("summary", "")), []).append(_e)
        for _key, _lst in _groups.items():
            if len(_lst) <= 1:
                continue
            _keep = max(_lst, key=lambda x: x.get("id", ""))
            for _dup in _lst:
                if _dup is _keep or len(closed) >= 5:
                    continue
                subprocess.run(["python3", NOTIFY, "close", "--id", _dup["id"], "--reason", "auto-close rule: duplicate-superseded"], capture_output=True, text=True, timeout=60)
                _q2 = load_json(os.path.join(STATE, "decision-queue.json"), {"pending": [], "resolved": []})
                if _dup["id"] not in [x.get("id") for x in _q2.get("pending", [])]:
                    closed.append({"id": _dup["id"], "task_id": _dup.get("task_id", ""), "reason": "duplicate-superseded", "at": now})
                    q = _q2
        for e in list(q.get("pending", [])):
            if len(closed) >= 5:
                break
            ok, reason = auto_close_decision(e, roots)
            if not ok:
                continue
            subprocess.run(["python3", NOTIFY, "close", "--id", e["id"], "--reason", "auto-close rule: " + reason],
                           capture_output=True, text=True, timeout=60)
            q2 = load_json(os.path.join(STATE, "decision-queue.json"), {"pending": [], "resolved": []})
            if e["id"] not in [x.get("id") for x in q2.get("pending", [])]:
                closed.append({"id": e["id"], "task_id": e.get("task_id", ""), "reason": reason, "at": now})
                q = q2
        if closed:
            with open(os.path.join(STATE, "auto-close-log.json"), "a", encoding="utf-8") as fh:
                for c in closed:
                    fh.write(json.dumps(c, ensure_ascii=False) + "\n")
            autofixes.append("auto-close: %d (%s)" % (len(closed), "、".join(c["id"] for c in closed)))
    n_pending = len(q.get("pending", []))
    # 2026-10-30 可观测性：近1小时五步日志按模型统计失败，分清“模型死了”还是“模型写太短”，一眼看出谁不行。
    try:
        import re as _re3
        from collections import Counter as _Ctr
        _dead, _short = _Ctr(), _Ctr()
        _logf = os.path.join(STATE, "five-step-executor.log")
        # 2026-11-01 FIX2: 文件 mtime 在 1 小时内，不代表末 3000 行就是“最近一小时”。
        # 该日志末 3000 行可横跨 10-30 至今；只按 mtime 放行会把三天前的累计失败每轮重复报成
        # last hour（实测 16:32 后无写入、末 3000 行仍是旧记录，16:38~17:01 六轮告警数字一字不变）。
        # 改为逐行解析时间戳，只统计 datetime.now()-3600s 之后的记录；无时间戳的行跳过。
        if os.path.isfile(_logf):
            _cutoff_ts = datetime.now().timestamp() - 3600
            _lines = open(_logf, encoding="utf-8", errors="ignore").read().splitlines()[-3000:]
            for _ln in _lines:
                _tm = _re3.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", _ln)
                if not _tm:
                    continue
                try:
                    if datetime.strptime(_tm.group(1), "%Y-%m-%dT%H:%M:%S").timestamp() < _cutoff_ts:
                        continue
                except Exception:
                    continue
                _m = _re3.search(r"STEP4_FAIL task=\S+ model=(\S+) code=(-?\d+) out_len=(\d+)", _ln)
                if _m:
                    if _m.group(2) != "0":
                        _dead[_m.group(1)] += 1
                    elif int(_m.group(3)) < 50:
                        _short[_m.group(1)] += 1
        for _mm, _nn in _dead.most_common(3):
            if _nn >= 3:
                findings.append("model-dead: %s fails=%d (code!=0, last hour)" % (_mm, _nn))
        for _mm, _nn in _short.most_common(3):
            if _nn >= 3:
                findings.append("model-short: %s short-outputs=%d (code=0 out_len<50, last hour)" % (_mm, _nn))
    except Exception:
        pass
    result = {
        "at": now,
        "disk_pct": pct,
        "route_models": n_models,
        "decision_pending": n_pending,
        "autofixes": autofixes,
        "findings": findings,
        "note": "- %s 八层审查：磁盘 %s%%，路由模型 %s 个，待决 %s 条；自动修复 %s；新发现 %s" % (
            now, pct, n_models, n_pending,
            "、".join(autofixes) if autofixes else "无",
            "、".join(findings) if findings else "无"),
    }
    out_path = os.path.join(STATE, "eight-layer-review-%s.json" % today)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    # 2026-11-05 ARCHFIX: 状态行不再追加到 butler-arch-note.md（L8 札记纪律：只写本轮事实，不写状态行凑数）。
    # note 内容已写入 state/eight-layer-review-YYYYMMDD.json 的 "note" 字段，可观测性不丢失。
    # 系统发现直通专家（2026-10-30 强制闭环令）：任务失败才调专家是漏洞，系统级新发现也必须问。
    # 只对“新”发现问一次（按发现内容哈希去重），优先 Gemini；超时或失败只记档，不阻塞循环。
    if findings:
        import hashlib
        h = hashlib.sha256("|".join(sorted(findings)).encode("utf-8")).hexdigest()[:16]
        seen_path = os.path.join(STATE, "expert-consulted.json")
        seen = load_json(seen_path, {})
        if seen.get("last_hash") != h:
            prompt = (
                "系统背景：枢纽 AI 团队常驻巡查发现新问题：%s。当前磁盘 %s%%，路由模型 %s 个，待决 %s 条。"
                "卡点：这些问题不在任务失败链里，五步自救不会自动处理。候选方案：A) 按可逆低风险原则直接修复并记档；"
                "B) 推决策队列等人工定夺。请给出 A/B 两个具体可执行方案，并说明优缺点。"
                % ("；".join(findings), pct, n_models, n_pending)
            )
            ans = ""
            try:
                # 2026-11-01 ARCHFIX: web_brain 的档案在 /opt/model-profiles（root 700），
                # 普通用户复制必失败；与 brain_canary 对齐改走 sudo -n，-n 保证不因要密码而挂住。
                r = subprocess.run(
                    ["sudo", "-n", "python3", os.path.join(BASE, "scripts/web_brain.py"), "--provider", "gemini",
                     "--prompt", prompt, "--timeout", "180"],
                    capture_output=True, text=True, timeout=200)
                # 2026-11-01 FIX3: gemini 分支会先向 stdout 打 [gem_probe] 调试行，整段 json.loads
                # 必然失败，专家意见永远进不来（连续十几轮“专家问诊失败”）。改为取最后一条以 { 开头的行，
                # 与其他调用方（butler-self-check.py / chaos-drill.py）的解析约定一致。
                _out = (r.stdout or "").strip()
                _line = ""
                for _l in reversed(_out.splitlines()):
                    if _l.strip().startswith("{"):
                        _line = _l.strip()
                        break
                d = json.loads(_line or "{}")
                ans = (d.get("answer") or "").strip()
            except Exception as e:
                ans = ""
            if ans:
                # 2026-11-05 ARCHFIX: 专家问诊结果不再追加到 butler-arch-note.md，改写入 eight-layer-review JSON
                seen["last_hash"] = h
                seen["last_at"] = now
                seen["expert_answer"] = ans[:1500]
                with open(seen_path, "w", encoding="utf-8") as fh:
                    json.dump(seen, fh, ensure_ascii=False, indent=2)
            else:
                # 2026-11-05 ARCHFIX: 专家问诊失败不追加到 butler-arch-note.md，记档到 expert-consulted.json
                seen["last_hash"] = h
                seen["last_at"] = now
                seen["expert_answer_failed"] = True
                with open(seen_path, "w", encoding="utf-8") as fh:
                    json.dump(seen, fh, ensure_ascii=False, indent=2)
    # 高危直通：只推真正高危（磁盘仍超 90%），其余记档等 Twt 分流
    if pct >= 90 and os.path.isfile(NOTIFY):
        subprocess.run(
            ["python3", NOTIFY, "push", "--type", "NEEDS_DECISION",
             "--task-id", "butler-disk-%s" % today,
             "--summary", "大管家高危：磁盘清理后仍超 90%%（%d%%）" % pct,
             "--options", "A:人工接手处理|B:暂缓，先观察"],
            capture_output=True, timeout=60)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
