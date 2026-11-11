#!/usr/bin/env python3
"""pipeline-codegen-20261110.py — hub-side code generation step (worker-1 leg fix).

Wiring: verdict -> dispatch-gate -> [this step] -> INSERT READY -> provision
         -> worker-1 pytest -> verifier DONE.

What it does:
  1. Loads a task contract JSON.
  2. If a valid receipt already exists for this (task_id, contract_sha256),
     exits 0 without regenerating (idempotent).
  3. Otherwise calls ceo-opencode.sh (OpenCode wrapper, free channel) with a
     task prompt instructing it to write code + tests to
     state/codegen-workdir/<name>/.
  4. Validates the output (anti false-green): >=1 test file, .py AST parse,
     no trivially-true-only test file, and a hub-side pytest run in a temp
     dir that must collect >=1 test and exit 0.
  5. Writes a receipt to state/code-gen-20261106/out/<task>/receipt.json
     (the path pipeline-dispatch-20261106.py already reads). The receipt's
     files[].path entries are RELATIVE to base_dir (the codegen workdir);
     _build_manifest uses base_dir when present, else falls back to the
     receipt's own outdir (backward compatible with code-gen-20261106.py
     receipts).

Exit codes: 0 = generated (or valid receipt already present);
            1 = failed (nothing inserted by the caller; receipt ok=false);
            2 = SKIP (butler seat busy; NOT a failure, no retry consumed,
                caller must abort dispatch without inserting).
Timeouts: ceo-opencode.sh has its own 600s inner timeout. This script uses
660s and kills the whole process group on timeout (start_new_session=True),
so a hung wrapper cannot orphan the seat lock.
No secrets are printed. No DB writes. No queue claims.
"""
import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

BASE = "/home/ubuntu/ai-team"
CEO_OPENCODER = BASE + "/scripts/ceo-opencode.sh"
CODEGEN_WORKDIR = BASE + "/state/codegen-workdir"
RECEIPT_BASE = BASE + "/state/code-gen-20261106/out"
LOG_FILE = BASE + "/state/logs/codegen-20261110.log"
TIMEOUT_S = 660
LOCK_STALE_S = 900


def _ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def log(msg):
    line = "%s %s" % (_ts(), msg)
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _contract_sha(contract_path):
    return _sha(contract_path)


def _is_trivial_test(path):
    """True if every assert in the file is trivially true (assert True/1/1==1)."""
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except Exception:
        return False
    asserts = [n for n in ast.walk(tree) if isinstance(n, ast.Assert)]
    if not asserts:
        return False
    for a in asserts:
        t = a.test
        if isinstance(t, ast.Constant) and t.value is True:
            continue
        if isinstance(t, ast.Constant) and t.value == 1:
            continue
        if isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], ast.Eq):
            if isinstance(t.left, ast.Constant) and isinstance(t.comparators[0], ast.Constant):
                if t.left.value == t.comparators[0].value:
                    continue
        return False
    return True


def _validate(workdir):
    """Returns (ok, reason, detail). Anti false-green: real tests must exist."""
    if not os.path.isdir(workdir):
        return False, "no_workdir", workdir
    py_files = []
    test_files = []
    for root, _dirs, files in os.walk(workdir):
        for fn in sorted(files):
            if fn.endswith(".py"):
                p = os.path.join(root, fn)
                py_files.append(p)
                if fn.startswith("test_") or fn.endswith("_test.py"):
                    test_files.append(p)
    if not test_files:
        return False, "no_test_file", "workdir=%s py=%d" % (workdir, len(py_files))
    for p in py_files:
        try:
            ast.parse(open(p, encoding="utf-8").read())
        except SyntaxError as e:
            return False, "ast_fail", "%s: %s" % (p, e)
    trivial = [p for p in test_files if _is_trivial_test(p)]
    nontrivial = [p for p in test_files if p not in trivial]
    if not nontrivial:
        return False, "trivial_tests_only", "test_files=%s" % test_files
    # Hub-side pytest in a temp copy (keeps workdir free of __pycache__).
    tmp = tempfile.mkdtemp(prefix="codegen-pytest-")
    try:
        for item in os.listdir(workdir):
            s = os.path.join(workdir, item)
            d = os.path.join(tmp, item)
            if os.path.isdir(s):
                shutil.copytree(s, d)
            else:
                shutil.copy2(s, d)
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", tmp],
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout or "") + (r.stderr or "")
        m = re.search(r"(\d+) passed", out)
        collected = int(m.group(1)) if m else 0
        if r.returncode != 0 or collected < 1:
            return False, "hub_pytest_fail", "rc=%d collected=%d tail=%s" % (
                r.returncode, collected, out.strip()[-300:])
        return True, "ok", "collected=%d" % collected
    except subprocess.TimeoutExpired:
        return False, "hub_pytest_timeout", "120s"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def build_prompt(contract, workdir):
    tid = contract.get("task_id", "unknown")
    inp = contract.get("input", "")
    out_path = contract.get("output_path", "")
    lines = [
        "你是一个代码生成任务的执行者。只写代码和测试，不做别的。",
        "任务ID：%s" % tid,
        "工作目录（绝对路径，所有文件必须写到这里）：%s" % workdir,
        "任务描述：",
        inp,
    ]
    if out_path:
        lines.append("交付物主文件（若适用）：%s" % out_path)
    lines += [
        "硬性要求：",
        "1. 只写工作目录下的相对路径文件，不写工作目录之外，不改其他文件。",
        "2. 必须写至少一个测试文件 test_*.py，测试必须真实断言被测代码的行为，不许只写 assert True。",
        "3. 所有 .py 文件必须能通过 AST 解析，无语法错误。",
        "4. 写完后在工作目录内确认文件都在，不要只口头说写好了。",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", required=True)
    ap.add_argument("--timeout", type=int, default=TIMEOUT_S)
    a = ap.parse_args()
    contract = json.load(open(a.contract))
    tid = contract.get("task_id", "unknown")
    csha = _contract_sha(a.contract)
    wd_name = os.path.basename((contract.get("workdir") or "").rstrip("/")) or tid
    workdir = os.path.join(CODEGEN_WORKDIR, wd_name)
    receipt_dir = os.path.join(RECEIPT_BASE, tid)
    receipt_path = os.path.join(receipt_dir, "receipt.json")

    # Idempotent: valid receipt for this contract sha already exists.
    if os.path.exists(receipt_path):
        try:
            rec = json.load(open(receipt_path))
            if rec.get("contract_sha256") == csha and rec.get("ok") is True:
                base = rec.get("base_dir") or receipt_dir
                files_ok = all(
                    os.path.exists(os.path.join(base, f.get("path", "")))
                    for f in rec.get("files", []))
                if files_ok and rec.get("files"):
                    log("CODEGEN_SKIP receipt_valid task=%s" % tid)
                    return 0
        except Exception:
            pass

    prompt = build_prompt(contract, workdir)
    os.makedirs(workdir, exist_ok=True)
    log("CODEGEN_START task=%s workdir=%s" % (tid, workdir))
    t0 = time.time()
    p = subprocess.Popen(
        ["bash", CEO_OPENCODER, prompt],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        start_new_session=True)
    try:
        out, _ = p.communicate(timeout=a.timeout)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        # Child is a process-group leader (start_new_session=True), so its
        # pgid == its pid. Kill the whole group: a hung opencode must not
        # survive and orphan the seat lock.
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except Exception:
            pass
        try:
            out, _ = p.communicate(timeout=30)
        except Exception:
            out = ""
        log("CODEGEN_TIMEOUT task=%s timeout_s=%d" % (tid, a.timeout))
        _write_receipt(receipt_dir, receipt_path, tid, csha, False,
                       "codegen_timeout", [], None)
        return 1
    dt = time.time() - t0
    log("CODEGEN_WRAPPER_DONE task=%s rc=%d secs=%.1f" % (tid, rc, dt))
    if "[SKIP]" in out:
        log("CODEGEN_SKIP seat_busy task=%s" % tid)
        return 2
    if rc != 0:
        log("CODEGEN_FAIL wrapper_rc=%d task=%s tail=%s" % (rc, tid, out.strip()[-300:]))
        _write_receipt(receipt_dir, receipt_path, tid, csha, False,
                       "wrapper_rc_%d" % rc, [], None)
        return 1

    ok, reason, detail = _validate(workdir)
    log("CODEGEN_VALIDATE task=%s ok=%s reason=%s detail=%s" % (tid, ok, reason, detail))
    if not ok:
        _write_receipt(receipt_dir, receipt_path, tid, csha, False,
                       reason, [], detail)
        return 1

    files = []
    for root, _dirs, fns in os.walk(workdir):
        for fn in sorted(fns):
            p = os.path.join(root, fn)
            if os.path.isfile(p):
                files.append({"path": os.path.relpath(p, workdir),
                              "sha256": _sha(p),
                              "bytes": os.path.getsize(p)})
    _write_receipt(receipt_dir, receipt_path, tid, csha, True, "ok", files,
                   detail, base_dir=workdir)
    log("CODEGEN_OK task=%s files=%d secs=%.1f" % (tid, len(files), dt))
    return 0


def _write_receipt(receipt_dir, receipt_path, tid, csha, ok, reason, files,
                   detail, base_dir=None):
    os.makedirs(receipt_dir, exist_ok=True)
    rec = {"task_id": tid, "contract_sha256": csha, "ok": ok,
           "reason": reason, "files": files, "detail": detail,
           "created_at": _ts(), "generator": "pipeline-codegen-20261110.py",
           "model": "ceo-opencode.sh"}
    if base_dir:
        # Files live under base_dir (state/codegen-workdir/<name>/); paths in
        # files[] are relative to it. _build_manifest uses base_dir when
        # present, else falls back to the receipt's own outdir (backward
        # compatible with code-gen-20261106.py receipts).
        rec["base_dir"] = base_dir
    tmp = receipt_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    os.rename(tmp, receipt_path)


if __name__ == "__main__":
    sys.exit(main())
