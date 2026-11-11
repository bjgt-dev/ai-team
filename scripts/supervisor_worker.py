#!/usr/bin/env python3
"""监工（supervisor worker）——枢纽内，脚本 + 模型网关小模型。

定案来源（2026-10-27）：枢纽内，脚本+网关小模型，盯工人干活、
判产物合不合格、转 UNKNOWN 上报；优先给大管家（活着前提下，
patrol/ACK 5 分钟内刷新），否则给 Twt。问主是大管家/Twt，
本脚本绝不自动问专家。

职责边界（只做这一轨）：
- 只判，不执行：不跑工人命令、不改大管家席位配置、不碰
  /opt/model-profiles、不花钱、不对外发布、不做不可逆操作。
- 只读产物清单做确定性检查：文件存在、sha256、回执四字段、
  测试退出码；不执行 manifest 里的任何命令。
- 模型网关调用契约（硬规则，防挂起）：
  hub_chat.py 的模型名只走 argv[1]，prompt 只走 stdin，
  绝不把 prompt 当位置参数。单次调用，429 即硬停：不重试、
  不换模型、不换路，直接记 UNKNOWN(gateway_429_hard_stop)。
- 证据齐、可逆、影响小才自己判（确定性 PASS/REJECT）；
  能力不够、影响大、不可逆、对外发布、连续判错（>=2 次）
  一律转 UNKNOWN 并写待复核记录，不自动问专家。
- 本脚本是单次运行（one-shot），不是常驻进程：跑完即退出，
  “能停”= 无后台残留；--cleanup 只删自测临时目录，
  且路径必须落在白名单前缀内，否则拒绝。

用法：
  python3 supervisor_worker.py --manifest manifest.json [--out-dir DIR]
  python3 supervisor_worker.py --selftest [--out-dir DIR]   # 不调用网关
  python3 supervisor_worker.py --cleanup DIR                # 只删自测目录

Manifest（JSON）字段：
  task_id: str, impact: "small"|"large", reversible: bool,
  external_publish: bool, consecutive_failures: int,
  artifacts: [{path, sha256}],  # sha256 可空，空则只验存在
  receipt: {tool_name, status, output_sha256, created_at},
  test_exit_code: int
输出：out-dir/verdict-<task_id>.json + state 审计 JSONL + UNKNOWN 时
待复核记录 pending/<task_id>.json。审计与证据目录默认：
  /home/ubuntu/ai-team/state/supervisor-worker-20261027/
"""
import hashlib
import json
import re
import os
import subprocess
import sys
import time

HUB = "/home/ubuntu/ai-team"
STATE_DIR = os.path.join(HUB, "state", "supervisor-worker-20261027")
AUDIT_FILE = os.path.join(STATE_DIR, "audit.jsonl")
PENDING_DIR = os.path.join(STATE_DIR, "pending")
HUB_CHAT = os.path.join(HUB, "scripts", "hub_chat.py")
PATROL_FILES = [
    os.path.join(HUB, "ceo-seat", "out", "patrol-latest.md"),
    os.path.join(HUB, "state", "dispatch-ack.log"),
]
FRESH_SECONDS = 300
DEFAULT_MODEL = "glm-4-flash-cc"
MODEL_TIMEOUT = 180


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def ensure_state():
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(PENDING_DIR, exist_ok=True)


def audit(record):
    ensure_state()
    record = dict(record, audited_at=now_iso())
    with open(AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def next_owner():
    """大管家活着（任一 patrol/ACK 文件 5 分钟内刷新）则优先给大管家，否则给 Twt。"""
    now = time.time()
    fresh = []
    for p in PATROL_FILES:
        try:
            age = now - os.path.getmtime(p)
        except OSError:
            continue
        fresh.append((p, round(age)))
        if age <= FRESH_SECONDS:
            return "butler", {"patrol_file": p, "age_seconds": round(age)}
    return "Twt", {"checked_files": fresh}


def deterministic_checks(manifest):
    """返回 (passed: bool, failures: [str], evidence: dict)。只做确定性检查。"""
    failures, evidence = [], {}
    receipt = manifest.get("receipt") or {}
    for field in ("tool_name", "status", "output_sha256", "created_at"):
        if not receipt.get(field):
            failures.append(f"receipt_missing:{field}")
    if receipt.get("output_sha256") and len(str(receipt["output_sha256"])) != 64:
        failures.append("receipt_output_sha256_not_64_hex")
    tec = manifest.get("test_exit_code")
    if tec != 0:
        failures.append(f"test_exit_code:{tec!r} (expected 0)")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        failures.append("artifacts_empty_or_missing")
        artifacts = []
    checked = []
    for art in artifacts:
        path = (art or {}).get("path", "")
        want = (art or {}).get("sha256", "")
        if not path or not os.path.isfile(path):
            failures.append(f"artifact_missing:{path}")
            checked.append({"path": path, "exists": False})
            continue
        got = sha256_of(path)
        ok = (not want) or (got == want)
        if not ok:
            failures.append(f"artifact_hash_mismatch:{path}")
        checked.append({"path": path, "exists": True,
                        "sha256": got, "sha256_match": ok})
    evidence["artifacts_checked"] = checked
    evidence["receipt_present"] = all(receipt.get(f) for f in
                                      ("tool_name", "status", "output_sha256", "created_at"))
    evidence["test_exit_code"] = tec
    return (not failures), failures, evidence



def extract_json_text(text):
    """剥离 markdown 围栏后返回 JSON 文本；无围栏则原样返回。只做解析预处理，不改 verdict 语义。"""
    s = (text or "").strip()
    m = re.search(r"```[A-Za-z]*\s*(\{.*?\})\s*```", s, re.DOTALL)
    if m:
        return m.group(1).strip()
    return s


def call_gateway_model(prompt, model, hub_chat=HUB_CHAT):
    """单次调用。返回 (verdict|None, info dict)。429/错误/空/解析失败 -> None，绝不重试。"""
    try:
        proc = subprocess.run(
            [sys.executable, hub_chat, model],
            input=prompt, capture_output=True, text=True, timeout=MODEL_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, {"gateway": "timeout", "model": model,
                      "note": "single attempt, no retry"}
    except Exception as exc:  # noqa: BLE001 - 只记录类型，不扩散细节
        return None, {"gateway": "invoke_error", "model": model,
                      "error_type": type(exc).__name__}
    out, err = proc.stdout or "", proc.stderr or ""
    if "429" in err or "429" in out or proc.returncode != 0 and "429" in err:
        return None, {"gateway": "429_hard_stop", "model": model,
                      "note": "no retry, no model switch, no alternate route"}
    if proc.returncode != 0 or not out.strip():
        return None, {"gateway": "unavailable", "model": model,
                      "returncode": proc.returncode,
                      "stderr_tail": err.strip()[-300:]}
    # 结构化解析：优先 JSON（先剥离 markdown 围栏），其次首个 PASS/REJECT/UNKNOWN 行
    # 修复 2026-10-27：只改解析，不改判断逻辑；围栏 JSON 此前落入 unparseable。
    try:
        data = json.loads(extract_json_text(out))
        v = str(data.get("verdict", "")).strip().upper()
        if v in ("PASS", "REJECT", "UNKNOWN"):
            return v, {"gateway": "ok", "model": model, "raw": out.strip()[:500]}
    except Exception:
        pass
    for line in out.splitlines():
        token = line.strip().upper()
        if token in ("PASS", "REJECT", "UNKNOWN") or token.startswith(("PASS ", "REJECT ", "UNKNOWN ")):
            return token.split()[0], {"gateway": "ok", "model": model,
                                      "raw": out.strip()[:500]}
    return None, {"gateway": "unparseable", "model": model,
                  "raw": out.strip()[:500]}


def write_pending(task_id, reason, manifest, evidence, model_info):
    owner, owner_evidence = next_owner()
    rec = {"task_id": task_id, "verdict": "UNKNOWN", "reason": reason,
           "next_owner": owner, "owner_evidence": owner_evidence,
           "evidence": evidence, "model_info": model_info,
           "note": "DO NOT auto-ask expert panel; question owner is butler/Twt.",
           "created_at": now_iso()}
    path = os.path.join(PENDING_DIR, f"{task_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    return path


def judge(manifest, model=DEFAULT_MODEL, out_dir=None):
    """核心判断。返回 (verdict, verdict_path, info)。"""
    ensure_state()
    out_dir = out_dir or STATE_DIR
    os.makedirs(out_dir, exist_ok=True)
    task_id = str(manifest.get("task_id") or "unknown-task")
    passed, failures, evidence = deterministic_checks(manifest)
    evidence["impact"] = manifest.get("impact")
    evidence["reversible"] = manifest.get("reversible")
    evidence["external_publish"] = manifest.get("external_publish")
    evidence["consecutive_failures"] = manifest.get("consecutive_failures", 0)

    # 1) 影响大 / 不可逆 / 对外发布 / 连续判错 -> UNKNOWN，不调用模型
    escalate = []
    if manifest.get("impact") == "large":
        escalate.append("impact_large")
    if manifest.get("reversible") is False:
        escalate.append("irreversible")
    if manifest.get("external_publish") is True:
        escalate.append("external_publish")
    if isinstance(manifest.get("consecutive_failures"), int) and manifest["consecutive_failures"] >= 2:
        escalate.append("consecutive_failures>=2")
    if escalate:
        reason = "escalated:" + ",".join(escalate)
        pending = write_pending(task_id, reason, manifest, evidence, {"gateway": "not_called"})
        verdict = {"task_id": task_id, "verdict": "UNKNOWN", "reason": reason,
                   "deterministic_passed": passed, "deterministic_failures": failures,
                   "pending_record": pending, "created_at": now_iso()}
        path = os.path.join(out_dir, f"verdict-{task_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(verdict, f, ensure_ascii=False, indent=2)
        audit({"task_id": task_id, "verdict": "UNKNOWN", "reason": reason,
               "verdict_path": path, "pending_record": pending})
        return "UNKNOWN", path, verdict

    # 2) 确定性失败 -> REJECT，不调用模型（确定性证据为准）
    if not passed:
        verdict = {"task_id": task_id, "verdict": "REJECT", "reason": "deterministic_failures",
                   "deterministic_failures": failures, "evidence": evidence,
                   "created_at": now_iso()}
        path = os.path.join(out_dir, f"verdict-{task_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(verdict, f, ensure_ascii=False, indent=2)
        audit({"task_id": task_id, "verdict": "REJECT", "reason": "deterministic_failures",
               "verdict_path": path})
        return "REJECT", path, verdict

    # 3) 证据齐、可逆、影响小 -> 网关小模型做结构化判断（单次，stdin 传 prompt）
    prompt = ("You are a supervisor checker. Reply with exactly one JSON object: "
              '{"verdict": "PASS"|"REJECT"|"UNKNOWN", "reason": "..."}. '
              "Judge only whether the deterministic evidence below is complete and "
              "the task is small and reversible. Evidence: " +
              json.dumps(evidence, ensure_ascii=False)[:2000])
    model_verdict, model_info = call_gateway_model(prompt, model)
    if model_verdict is None:
        reason = "gateway_unavailable:" + str(model_info.get("gateway"))
        pending = write_pending(task_id, reason, manifest, evidence, model_info)
        verdict = {"task_id": task_id, "verdict": "UNKNOWN", "reason": reason,
                   "evidence": evidence, "model_info": model_info,
                   "pending_record": pending, "created_at": now_iso()}
        path = os.path.join(out_dir, f"verdict-{task_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(verdict, f, ensure_ascii=False, indent=2)
        audit({"task_id": task_id, "verdict": "UNKNOWN", "reason": reason,
               "verdict_path": path, "pending_record": pending})
        return "UNKNOWN", path, verdict
    verdict = {"task_id": task_id, "verdict": model_verdict, "reason": "gateway_model",
               "evidence": evidence, "model_info": model_info, "created_at": now_iso()}
    path = os.path.join(out_dir, f"verdict-{task_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(verdict, f, ensure_ascii=False, indent=2)
    audit({"task_id": task_id, "verdict": model_verdict, "reason": "gateway_model",
           "verdict_path": path})
    return model_verdict, path, verdict


def safe_cleanup(path):
    """只删自测目录：必须在 STATE_DIR/selftest-* 或 /tmp/supervisor-worker-test-* 下。"""
    ap = os.path.abspath(path)
    ok = (ap.startswith(os.path.join(STATE_DIR, "selftest-")) or
          ap.startswith("/tmp/supervisor-worker-test-"))
    if not ok:
        print(f"REFUSE_CLEANUP outside allowlist: {ap}", file=sys.stderr)
        return False
    import shutil
    if os.path.isdir(ap):
        shutil.rmtree(ap)
    elif os.path.exists(ap):
        os.remove(ap)
    return True


def selftest(out_dir):
    """不调用网关的自测：静默检查 -> 判断 -> 验收写盘 -> 清理。

    覆盖三条不调用网关的路径：(a) 影响大 -> UNKNOWN 待复核；
    (b) 哈希不符 -> REJECT；(c) 文件缺失 -> REJECT。
    网关 PASS 路径因 429 硬停规则在本轮不实测，代码路径保留，
    待网关恢复后由问主重跑，绝不虚构网关记录。
    """
    ensure_state()
    base = os.path.join(STATE_DIR, "selftest-" + time.strftime("%Y%m%d%H%M%S"))
    os.makedirs(base, exist_ok=True)
    results = []
    try:
        artifact = os.path.join(base, "fake-output.txt")
        with open(artifact, "w", encoding="utf-8") as f:
            f.write("supervisor-worker selftest fake artifact\n")
        good_hash = sha256_of(artifact)
        receipt = {"tool_name": "fake-worker", "status": "done",
                   "output_sha256": good_hash, "created_at": now_iso()}
        cases = [
            ("selftest-large-impact", {"impact": "large", "reversible": True,
                                       "external_publish": False, "consecutive_failures": 0,
                                       "artifacts": [{"path": artifact, "sha256": good_hash}],
                                       "receipt": receipt, "test_exit_code": 0}, "UNKNOWN"),
            ("selftest-bad-hash", {"impact": "small", "reversible": True,
                                   "external_publish": False, "consecutive_failures": 0,
                                   "artifacts": [{"path": artifact, "sha256": "0" * 64}],
                                   "receipt": receipt, "test_exit_code": 0}, "REJECT"),
            ("selftest-missing-file", {"impact": "small", "reversible": True,
                                       "external_publish": False, "consecutive_failures": 0,
                                       "artifacts": [{"path": os.path.join(base, "no-such-file.txt"),
                                                      "sha256": good_hash}],
                                       "receipt": receipt, "test_exit_code": 0}, "REJECT"),
        ]
        for task_id, manifest, expected in cases:
            manifest = dict(manifest, task_id=task_id)
            verdict, path, info = judge(manifest, out_dir=base)
            ok = verdict == expected
            results.append({"case": task_id, "expected": expected, "got": verdict,
                            "verdict_path": path, "pass": ok})
            if not ok:
                raise AssertionError(f"{task_id}: expected {expected}, got {verdict}")
        # 验收：审计行与 UNKNOWN 待复核记录存在
        pending = os.path.join(PENDING_DIR, "selftest-large-impact.json")
        assert os.path.isfile(pending), "pending record missing"
        assert os.path.isfile(AUDIT_FILE), "audit file missing"
        results.append({"case": "audit_and_pending_exist", "pass": True,
                        "pending_record": pending, "audit_file": AUDIT_FILE})
        report = {"selftest": "PASS", "results": results, "workdir": base,
                  "note": "gateway PASS path NOT tested this run (429 hard stop); no gateway record fabricated.",
                  "created_at": now_iso()}
        report_path = os.path.join(STATE_DIR, "selftest-report.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    finally:
        # 清理：只删自测工作目录；审计/待复核/报告保留作证据
        safe_cleanup(base)


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="supervisor worker (hub, one-shot)")
    ap.add_argument("--manifest", help="task manifest JSON path")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--cleanup", help="only allowlisted selftest dir")
    args = ap.parse_args(argv)
    if args.cleanup:
        ok = safe_cleanup(args.cleanup)
        print(json.dumps({"cleanup": ok, "path": args.cleanup}, ensure_ascii=False))
        return 0 if ok else 2
    if args.selftest:
        return selftest(args.out_dir)
    if not args.manifest:
        ap.error("--manifest required unless --selftest/--cleanup")
    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)
    verdict, path, info = judge(manifest, model=args.model, out_dir=args.out_dir)
    print(json.dumps({"verdict": verdict, "verdict_path": path}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
