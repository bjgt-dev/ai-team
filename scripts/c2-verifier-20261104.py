#!/usr/bin/env python3
"""C2 W1精简版 verifier timer (2026-11-04, 令12实施).
每分钟跑一次, 只核验 VERIFYING 任务: fencing 与 ACTIVE lease 一致, sha 为 64 hex,
bytes >= min_bytes, llm_doc 文件存在且 sha/bytes 与任务一致.
code 类另过 gates 2/3/4（verifier_gates234）：AST 非空、临时目录重跑合同测试、红绿检查；逐条回执见 state/verifier-verdicts/<task_id>.json。
通过: 设 verified_by=c2-verifier, VERIFYING -> DONE, lease RELEASED.
失败: VERIFYING -> RETRY_WAIT, retry_count+1, lease RELEASED. (F2 2026-11-10: retry>=max -> QUARANTINED, no +1.)
DSN 只从 state/.cq-dsn 读, 无 DSN 则 fail closed.
"""
import hashlib, json, os, re, subprocess, sys
DSN_FILE = "/home/ubuntu/ai-team/state/.cq-dsn"
DELIV_DIR = "/home/ubuntu/ai-team/state/deliverables"
# M5-2 (2026-11-05): cross-supplier review hook for deliverable tasks.
REVIEW_HOOK = "/home/ubuntu/ai-team/scripts/c2-review-hook-20261105.py"
REVIEW_DIR = "/home/ubuntu/ai-team/state/m5-20261105/review"
REVIEW_HOOK_DISABLE = "/home/ubuntu/ai-team/state/m5-20261105/REVIEW-HOOK-DISABLED"
# Method-07 (2026-11-05): rework children + incident drafts on review FAIL.
METHOD07_DIR = "/home/ubuntu/ai-team/state/method-07-review"
REWORK_INCIDENT_DIR = METHOD07_DIR + "/rework-incidents"
REWORK_MAX_PER_ROOT = 2
SHA64 = re.compile(r"^[0-9a-fA-F]{64}$")
# Gates 2/3/4 (2026-11-06, verifier-gates-234): AST + rerun + red/green for code tasks.
try:
    import verifier_gates234 as _GATES234
except Exception:
    _GATES234 = None
VERDICT_DIR = "/home/ubuntu/ai-team/state/verifier-verdicts"

def _review_fail_reasons(task_id):
    """Best-effort read of the review record's reasons for this task."""
    try:
        with open(os.path.join(REVIEW_DIR, "review-%s.json" % task_id), encoding="utf-8") as f:
            rec = json.load(f)
        rs = rec.get("reasons")
        return [str(x) for x in rs] if isinstance(rs, list) else []
    except Exception:
        return []


def _create_rework_or_incident(cur, task_id, payload, lane, min_bytes):
    """Method-07: review FAIL generates a rework child (max 2 per root).

    Returns (child_task_id or None, incident_path or None). The child is a new
    READY llm_doc task carrying parent_task_id + review failure reasons +
    rework_root_id so the cap is enforced per root, not per level. Its content
    is the failed original plus a rework header, flagged
    rework_pending_reproduce=true: it must be re-produced (content replaced)
    before it can pass review; as-is it will FAIL again, which is how the
    chain demonstrates the cap. At the cap, no child is created; instead an
    incident draft JSON is written (no DB row is deleted or altered).
    """
    import datetime
    contract = payload.get("contract") if isinstance(payload, dict) else None
    if not isinstance(contract, dict):
        return None, None
    root = contract.get("rework_root_id") or str(task_id)
    reasons = _review_fail_reasons(task_id)
    try:
        cur.execute("SELECT count(*) FROM central_tasks WHERE payload->'contract'->>'rework_root_id' = %s", (root,))
        existing = cur.fetchone()[0]
    except Exception:
        existing = 0
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if existing >= REWORK_MAX_PER_ROOT:
        try:
            os.makedirs(REWORK_INCIDENT_DIR, exist_ok=True)
            ipath = os.path.join(REWORK_INCIDENT_DIR, "%s.json" % root)
            with open(ipath, "w", encoding="utf-8") as f:
                json.dump({"root_task_id": root, "failed_task_id": str(task_id),
                           "ts_utc": ts, "rework_attempts": existing,
                           "verdict": "REWORK_EXHAUSTED",
                           "review_failure_reasons": reasons,
                           "action": "DEAD + incident: no further automatic rework child; needs human/worker decision"},
                          f, ensure_ascii=False, indent=2)
            print(f"c2-verifier: rework EXHAUSTED root={root} attempts={existing} incident={ipath}")
            return None, ipath
        except Exception as e:
            print(f"c2-verifier: rework incident write failed root={root}: {e}")
            return None, None
    attempt = existing + 1
    child_contract = dict(contract)
    child_contract.update({"rework_attempt": attempt, "rework_root_id": root,
                           "parent_task_id": str(task_id),
                           "review_failure_reasons": reasons,
                           "rework_pending_reproduce": True,
                           "review_required": True})
    header = ("# 返工件（方法7自动生成，待按复核意见重做）\n\n"
              "- 父任务：%s\n- 返工第 %d 次（上限 %d 次）\n- 复核 FAIL 原因：%s\n"
              "- 注意：以下为 FAIL 原稿原样，待制作方按复核意见重做并替换 content；未重做前再次复核仍会 FAIL。\n\n"
              % (task_id, attempt, REWORK_MAX_PER_ROOT, "；".join(reasons) if reasons else "（见复核记录）"))
    content = header + (payload.get("content") or "")
    child_payload = {"type": "llm_doc", "content": content, "contract": child_contract}
    # queue-harden step B (2026-11-06): idempotency key "rework:<root>:<attempt>".
    # A retry of the same verifier run returns the existing child, never a twin.
    idem_key = "rework:%s:%d" % (root, attempt)
    cur.execute("INSERT INTO central_tasks (title, payload, lane, min_bytes, idempotency_key) VALUES (%s, %s::jsonb, %s, %s, %s) ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING RETURNING task_id",
                ("METHOD07-REWORK root=%s attempt=%d parent=%s" % (root[:8], attempt, str(task_id)[:8]),
                 json.dumps(child_payload, ensure_ascii=False), lane or "infra", min_bytes or 100, idem_key))
    got = cur.fetchone()
    if got is None:
        cur.execute("SELECT task_id FROM central_tasks WHERE idempotency_key = %s", (idem_key,))
        child_id = str(cur.fetchone()[0])
        print(f"c2-verifier: rework child already exists {child_id} for parent {task_id} attempt={attempt}/{REWORK_MAX_PER_ROOT} root={root} (idempotency key {idem_key})")
        return child_id, None
    child_id = str(got[0])
    print(f"c2-verifier: rework child {child_id} for parent {task_id} attempt={attempt}/{REWORK_MAX_PER_ROOT} root={root}")
    return child_id, None



# Thorsten Ball Rule 2 (2026-11-07): slop tripwire for code tasks.
# Three deterministic triggers; any one trips -> REJECT (RETRY_WAIT).
# Baselines come from the contract: test_baseline_seconds,
# allowed_dependencies. Without a declared baseline the corresponding
# sub-check is skipped (no baseline -> cannot detect slop -> no false
# positives on normal submissions).
SLOP_JUNK_NAME = re.compile(r"^(tmp|temp|test\d+|foo|bar|baz|untitled|file\d+|xxx|scratch|junk|dummy|asdf)([._-]|$)", re.IGNORECASE)
SLOP_SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".pytest_cache", ".venv", "venv"}
SLOP_SKIP_SUFFIX = {".pyc", ".pyo"}
SLOP_MAX_FILES = 1000

def _parse_pytest_seconds(text):
    """Last 'in Ns' duration in a pytest-style summary, or None."""
    ms = re.findall(r"in\s+(\d+(?:\.\d+)?)s\b", text)
    if not ms:
        return None
    try:
        return float(ms[-1])
    except ValueError:
        return None


def _parse_test_counts(text):
    """Structured test counts (cutover51 W5.1): parse pytest/junit summaries
    into {passed, failed, errors, collected} instead of substring hits.
    Returns None when the text carries no test-count evidence at all."""
    if not isinstance(text, str):
        text = str(text or "")
    passed = failed = errors = collected = None
    m = re.search(r"(\d+)\s+passed", text)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+)\s+failed", text)
    if m:
        failed = int(m.group(1))
    m = re.search(r"(\d+)\s+error", text)
    if m:
        errors = int(m.group(1))
    m = re.search(r"collected\s+(\d+)\s+items?", text)
    if m:
        collected = int(m.group(1))
    m = re.search(r"tests=\"(\d+)\"\s+failures=\"(\d+)\"\s+errors=\"(\d+)\"", text)
    if m:
        collected, failed, errors = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if passed is None:
            passed = collected - failed - errors
    if "no tests ran" in text.lower():
        collected = 0
        passed = 0
    if passed is None and failed is None and errors is None and collected is None:
        return None
    return {"passed": passed or 0, "failed": failed or 0,
            "errors": errors or 0, "collected": collected}

def _manifest_deps(workdir):
    """Dependency names declared in workdir manifests (requirements.txt,
    pyproject.toml, package.json). Returns a set of lowercase names."""
    deps = set()
    try:
        req = os.path.join(workdir, "requirements.txt")
        if os.path.isfile(req):
            for line in open(req, encoding="utf-8", errors="ignore"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                name = re.split(r"[=<>!~\s;\[]", line, 1)[0].strip().lower()
                if name:
                    deps.add(name)
        pyproj = os.path.join(workdir, "pyproject.toml")
        if os.path.isfile(pyproj):
            txt = open(pyproj, encoding="utf-8", errors="ignore").read()
            for m in re.finditer(r'"([A-Za-z0-9_.-]+)\s*[=<>!~]', txt):
                deps.add(m.group(1).lower())
            for m in re.finditer(r"'([A-Za-z0-9_.-]+)\s*[=<>!~]", txt):
                deps.add(m.group(1).lower())
        pkg = os.path.join(workdir, "package.json")
        if os.path.isfile(pkg):
            try:
                pj = json.load(open(pkg, encoding="utf-8"))
                for sec in ("dependencies", "devDependencies"):
                    d = pj.get(sec)
                    if isinstance(d, dict):
                        deps.update(k.lower() for k in d)
            except Exception:
                pass
    except Exception:
        pass
    return deps

def slop_tripwire(contract, workdir, artifact_data):
    """Return a list of slop trip reasons (empty = clean). contract is the
    task contract dict; workdir is the task workdir string (may be None);
    artifact_data is the deliverable bytes."""
    trips = []
    if not isinstance(contract, dict):
        contract = {}
    text = artifact_data.decode("utf-8", errors="ignore") if isinstance(artifact_data, (bytes, bytearray)) else str(artifact_data or "")
    # 1. Test runtime vs baseline (+20%).
    baseline = contract.get("test_baseline_seconds")
    if isinstance(baseline, (int, float)) and baseline > 0:
        runtime = _parse_pytest_seconds(text)
        if runtime is not None and runtime > baseline * 1.2:
            trips.append("test runtime %.2fs exceeds baseline %.2fs by >20%%" % (runtime, baseline))
    # 2. New dependency not in contract allowlist.
    allowed = contract.get("allowed_dependencies")
    wd = None
    if isinstance(workdir, str) and workdir.strip():
        wd = os.path.expanduser(workdir.strip())
    if isinstance(allowed, list) and wd and os.path.isdir(wd):
        allowed_set = {str(a).strip().lower() for a in allowed if str(a).strip()}
        found = _manifest_deps(wd)
        extra = sorted(found - allowed_set)
        if extra:
            trips.append("new dependencies not in contract allowlist: %s" % ", ".join(extra))
    # 3. Unexplained files: junk names or empty source files in workdir.
    if wd and os.path.isdir(wd):
        seen = 0
        for root, dirs, files in os.walk(wd):
            dirs[:] = [d for d in dirs if d not in SLOP_SKIP_DIRS]
            for fn in files:
                seen += 1
                if seen > SLOP_MAX_FILES:
                    break
                if any(fn.endswith(suf) for suf in SLOP_SKIP_SUFFIX):
                    continue
                fpath = os.path.join(root, fn)
                base = os.path.basename(fn)
                stem = os.path.splitext(base)[0]
                if SLOP_JUNK_NAME.match(stem):
                    trips.append("unexplained file: %s" % os.path.relpath(fpath, wd))
                    continue
                if base.endswith(".py") and os.path.getsize(fpath) == 0:
                    trips.append("empty source file: %s" % os.path.relpath(fpath, wd))
            if seen > SLOP_MAX_FILES:
                break
    return trips

def _workdir_isolation_check(cur, task_id, workdir):
    """Thorsten Ball Rule 7 (2026-11-07): return the task_id of another
    non-terminal task sharing the same normalized workdir, or None."""
    try:
        wdn = os.path.expanduser(workdir.strip()).rstrip("/")
        cur.execute(
            "SELECT task_id, payload->>'task_id', status, payload->>'workdir' FROM central_tasks "
            "WHERE status IN ('READY','CLAIMED','VERIFYING') AND payload->>'workdir' IS NOT NULL")
        for _row_tid, otid, _s, owd in cur.fetchall():
            if str(_row_tid) == str(task_id):
                continue
            if isinstance(owd, str) and os.path.expanduser(owd.strip()).rstrip("/") == wdn:
                return otid
    except Exception:
        return None
    return None

# Thorsten Ball Rule 9 (2026-11-07): evidence markers. A code artifact
# must carry at least one marker (test counts, sha256, pytest summary,
# exit code). ".md" deliverables are exempt: the doc itself is evidence.
EVIDENCE_MARKERS = [
    re.compile(r"\d+\s+passed"),
    re.compile(r"\d+\s+failed"),
    re.compile(r"\bOK\b"),
    re.compile(r"\bFAIL(ED|URE)?\b"),
    re.compile(r"sha256", re.IGNORECASE),
    re.compile(r"[0-9a-f]{64}"),
    re.compile(r"pytest"),
    re.compile(r"=+"),
    re.compile(r"pass3"),
    re.compile(r'"pass"\s*:\s*true'),
    re.compile(r"exit[=_ ]0"),
]

HUB_EXEC = "/home/ubuntu/ai-team/state/pull-once-20261105/hub_exec.py"
WORKDIR_MISS_FILE = "/home/ubuntu/ai-team/state/worker-workdir-miss.json"

def _workdir_exists_on_worker(workdir):
    """Check if workdir exists on worker-1 via hub_exec. Returns True if
    exists, False if missing, None if check itself failed (timeout/error)."""
    try:
        wd = (workdir or "").strip()
        if not wd or "code-agent-pilot/workdir" not in wd:
            return None
        cmd = "test -d %s && echo WORKDIR_EXISTS || echo WORKDIR_MISSING" % wd
        r = subprocess.run(["python3", HUB_EXEC], input=cmd,
                           capture_output=True, text=True, timeout=60)
        out = r.stdout or ""
        if "WORKDIR_EXISTS" in out:
            return True
        if "WORKDIR_MISSING" in out:
            return False
        return None
    except Exception:
        return None

def _workdir_miss_record(worker_id, missing):
    """Track consecutive workdir misses per worker. Returns the current
    consecutive miss count after recording this result. missing=True means
    this task had no workdir; missing=False resets the count to 0."""
    try:
        try:
            state = json.load(open(WORKDIR_MISS_FILE))
        except Exception:
            state = {}
        if not isinstance(state, dict):
            state = {}
        w = str(worker_id or "unknown")
        if missing:
            state[w] = int(state.get(w, 0)) + 1
        else:
            state[w] = 0
        with open(WORKDIR_MISS_FILE, "w") as f:
            json.dump(state, f)
        return state[w]
    except Exception:
        return 0


def _evidence_markers_ok(artifact_name, artifact_data):
    """Cutover51 W5.1: the .md blanket exemption is REMOVED. A .md deliverable
    must pass a content check (min 200 chars + at least one heading).
    Other artifacts: structured test counts decide when the artifact looks
    like test output (failed>0 or passed==0 -> not ok); otherwise at least
    one evidence marker must match."""
    text = artifact_data.decode("utf-8", errors="ignore") if isinstance(artifact_data, (bytes, bytearray)) else str(artifact_data or "")
    if str(artifact_name).lower().endswith(".md"):
        if len(text.strip()) < 200:
            return False
        return any(line.strip().startswith("#") for line in text.splitlines())
    counts = _parse_test_counts(text)
    if counts is not None:
        if counts["failed"] > 0 or counts["errors"] > 0:
            return False
        if counts["passed"] == 0:
            return False
        return True
    return any(p.search(text) for p in EVIDENCE_MARKERS)


def _gate_forbidden(contract, src_files):
    """Cutover51 W5.1: forbidden entries (contract list) must not appear
    among the delivered source files. A delivered file whose path or
    basename matches a forbidden entry is a reject (not a non-empty check)."""
    if not isinstance(contract, dict):
        return ("na", "no contract")
    fb = contract.get("forbidden")
    if isinstance(fb, str):
        fb = [p.strip() for p in fb.replace("\n", ",").split(",") if p.strip()]
    if not isinstance(fb, list) or not fb:
        return ("na", "no forbidden list declared")
    for f in (src_files or []):
        base = os.path.basename(str(f))
        for entry in fb:
            e = str(entry).strip()
            if not e:
                continue
            if e == base or e in str(f) or base == os.path.basename(e):
                return ("reject", "delivered file matches forbidden entry %r: %s" % (e, f))
    return ("pass", "no delivered file matches forbidden list")

# ---------------------------------------------------------------------------
# Five gates (state/verifier-five-gates-20261106.md), wired 2026-11-07.
# Each gate returns (verdict, detail) with verdict in {"pass","reject","na"}.
# "na" means the gate cannot apply to this task (no declared source / no
# declared test command) and is recorded, never silently skipped. A
# "reject" from any gate fails the task (RETRY_WAIT). Gates run before the
# M5 review hook: review can never override a deterministic gate failure.
#   1: contract.output_path / outputs[] exist, are files, non-empty.
#   2: AST parses and holds >=1 non-empty def/class (code .py sources).
#   3: re-run the contract tests in the E4 sandbox (netns, no network;
#      writes confined to the workdir) (only when the contract declares
#      test_command); submitted output is never trusted.
#   4: red-green: same tests must FAIL on a stub and PASS on the real
#      source (only when test_command is declared and a .py source exists).
#   5: deliverable set must contain a source file, not only test output;
#      llm_doc echo (deliverable == contract input) is rejected.
# ---------------------------------------------------------------------------
import ast as _ast
import shutil as _shutil
import shlex as _shlex
import tempfile as _tempfile

_GATE_TEST_TIMEOUT_DEFAULT = 120


def _norm_ws(text):
    """Collapse all whitespace (echo comparison for gate 5)."""
    if isinstance(text, (bytes, bytearray)):
        text = text.decode("utf-8", errors="ignore")
    return re.sub(r"\s+", "", str(text or ""))


def _is_local_path(value):
    """True when a contract output value looks like a local hub path.
    URLs and free-text notes are not paths: they are skipped (na), never
    treated as missing files."""
    if not isinstance(value, str) or not value.strip():
        return False
    v = value.strip()
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", v):
        return False
    return v.startswith(("/", "~", "./", "../")) or "/" in v or v.endswith((".py", ".md", ".txt", ".json", ".bin", ".sh"))


def _contract_output_paths(contract):
    """Local paths declared by the contract (output_path + outputs[])."""
    paths = []
    if not isinstance(contract, dict):
        return paths
    op = contract.get("output_path")
    if _is_local_path(op):
        paths.append(_abs_hub_path(op.strip()))
    outs = contract.get("outputs")
    if isinstance(outs, list):
        for o in outs:
            if isinstance(o, str) and _is_local_path(o):
                paths.append(_abs_hub_path(o.strip()))
            elif isinstance(o, dict) and _is_local_path(o.get("path")):
                paths.append(_abs_hub_path(o["path"].strip()))
    return paths


def _abs_hub_path(p):
    """Relative contract paths resolve against the hub base (the
    verifier's cwd under cron is not the contract's cwd)."""
    p = os.path.expanduser(p)
    if os.path.isabs(p):
        return p
    return os.path.join("/home/ubuntu/ai-team", p)


def _gate1_output_paths(contract):
    """Gate 1: every declared local output path exists, is a file, non-empty."""
    paths = _contract_output_paths(contract)
    if not paths:
        return ("na", "no local output_path declared")
    for p in paths:
        if not os.path.exists(p):
            return ("reject", "output_path missing: %s" % p)
        if not os.path.isfile(p):
            return ("reject", "output_path not a file: %s" % p)
        if os.path.getsize(p) == 0:
            return ("reject", "output_path empty: %s" % p)
    return ("pass", "%d output path(s) exist" % len(paths))


def _def_is_empty(node):
    """A def/class body is empty when it holds only pass / ... / docstring."""
    body = [n for n in node.body
            if not (isinstance(n, _ast.Expr) and isinstance(n.value, _ast.Constant)
                    and isinstance(n.value.value, str))]
    if not body:
        return True
    return all(isinstance(n, (_ast.Pass,)) or
               (isinstance(n, _ast.Expr) and isinstance(n.value, _ast.Constant)
                and n.value.value is Ellipsis)
               for n in body)


def _ast_check_py(path):
    """Gate 2 on one .py file. Returns (verdict, detail).
    parse fail -> reject; all defs/classes empty -> reject (skeleton);
    import-only with no def/class -> reject; data-only (assignments etc.,
    no def/class) -> pass with note (a constants module is not a stub)."""
    try:
        src = open(path, encoding="utf-8", errors="ignore").read()
    except OSError as e:
        return ("reject", "cannot read %s: %s" % (path, e))
    try:
        tree = _ast.parse(src, filename=path)
    except SyntaxError as e:
        return ("reject", "ast parse failed %s: %s" % (path, e))
    defs = [n for n in tree.body
            if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef))]
    if defs:
        if all(_def_is_empty(d) for d in defs):
            return ("reject", "skeleton %s: all %d def/class bodies empty" % (path, len(defs)))
        return ("pass", "%s: non-empty def/class present" % path)
    substantive = [n for n in tree.body
                   if not isinstance(n, (_ast.Import, _ast.ImportFrom))]
    if not substantive:
        return ("reject", "import-only %s: no function/class" % path)
    return ("pass", "%s: data-only module" % path)


def _py_source_candidates(contract, workdir):
    """Declared .py outputs first, then (best effort, hub-visible only)
    .py files under the task workdir, excluding test files. A workdir on
    worker-1 is not visible on the hub: then only declared outputs count."""
    cands = []
    for p in _contract_output_paths(contract):
        if p.endswith(".py"):
            cands.append(p)
    wd = None
    if isinstance(workdir, str) and workdir.strip():
        wd = os.path.expanduser(workdir.strip())
    if wd and os.path.isdir(wd):
        for root, dirs, files in os.walk(wd):
            dirs[:] = [d for d in dirs if d not in SLOP_SKIP_DIRS]
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                if fn.startswith("test_") or fn.endswith("_test.py") or fn == "conftest.py":
                    continue
                cands.append(os.path.join(root, fn))
                if len(cands) > 50:
                    return cands
    return cands


def _gate2_ast(contract, workdir):
    """Gate 2: AST non-empty check over the .py source candidates."""
    cands = _py_source_candidates(contract, workdir)
    if not cands:
        return ("na", "no .py source declared or visible")
    declared = set(_contract_output_paths(contract))
    for p in cands:
        verdict, detail = _ast_check_py(p)
        if verdict == "reject":
            # A workdir-discovered file that fails to PARSE is not the
            # deliverable's fault (scratch/old copies live there); only a
            # skeleton verdict rejects from workdir files. Declared
            # outputs reject on any failure.
            if "ast parse failed" in detail and p not in declared:
                continue
            return ("reject", detail)
    return ("pass", "%d .py source(s) checked" % len(cands))


def _test_command(contract):
    """Declared contract test command: str (shlex-split) or argv list.
    Returns argv list, or None when the contract declares no tests."""
    if not isinstance(contract, dict):
        return None
    cmd = contract.get("test_command")
    if isinstance(cmd, str) and cmd.strip():
        return _shlex.split(cmd)
    if isinstance(cmd, list) and cmd and all(isinstance(x, str) and x.strip() for x in cmd):
        return list(cmd)
    return None


# E4 sandbox (cutover51, 2026-11-10): test re-runs go through the userns
# netns launcher + LD_PRELOAD write filter, never directly. The launcher
# exits 125 (SANDBOX_SETUP_FAILED) when the sandbox cannot be built;
# that is a REJECT, never a silent fallback to a plain run.
_SANDBOX_LAUNCHER = "/home/ubuntu/ai-team/scripts/c2-verifier-sandbox.py"
_SANDBOX_BASE = os.environ.get("C2V_SANDBOX_BASE",
                               "/home/ubuntu/ai-team/state/verifier-sandbox")


def _run_in_fresh_dir(argv, files, timeout):
    """Copy {dest_basename: src_path} into a fresh sandbox workdir and run
    argv there under the E4 sandbox (no network, writes confined to the
    workdir). Returns (returncode, output tail); returncode None means
    timeout; returncode "sandbox-unavailable:<reason>" means the sandbox
    could not be built (fail closed, no plain-run fallback)."""
    try:
        os.makedirs(_SANDBOX_BASE, exist_ok=True)
    except OSError as e:
        return ("sandbox-unavailable: sandbox base not writable: %s" % e, "")
    tmp = _tempfile.mkdtemp(prefix="c2v-gate3-", dir=_SANDBOX_BASE)
    try:
        for dest, src in files.items():
            try:
                _shutil.copyfile(src, os.path.join(tmp, dest))
            except OSError:
                return ("missing-file:" + dest, "")
        if not os.path.isfile(_SANDBOX_LAUNCHER):
            return ("sandbox-unavailable: launcher missing: %s" % _SANDBOX_LAUNCHER, "")
        cmd = [sys.executable, _SANDBOX_LAUNCHER, "--workdir", tmp, "--"] + list(argv)
        try:
            r = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True,
                               timeout=timeout)
            out = ((r.stdout or "") + (r.stderr or ""))[-2000:]
            if r.returncode == 125 or "SANDBOX_SETUP_FAILED" in out:
                return ("sandbox-unavailable: %s" % out.strip()[-300:], "")
            return (r.returncode, out)
        except subprocess.TimeoutExpired:
            return (None, "timeout after %ss" % timeout)
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)


_MOCK_NET_IMPORTS = re.compile(
    r"^\s*(?:import\s+(?:requests|urllib3?|httpx|aiohttp|socket|http\.client)"
    r"|from\s+(?:urllib|urllib\.request|http\.client|socket|requests|httpx|aiohttp)\b"
    r"|.*\b(?:urlopen|requests\.|httpx\.|aiohttp\.|socket\.socket|socket\.create_connection)\b)",
    re.M)
_NET_CMDS = {"curl", "wget", "nc", "ncat", "ping", "ssh", "telnet", "aria2c"}


def _mock_declaration_ok(contract):
    """True when the contract declares mocks covering its external deps."""
    if not isinstance(contract, dict):
        return False
    if contract.get("mock") is True or contract.get("external_services_mocked") is True:
        return True
    mocks = contract.get("mocks") or contract.get("mock_services") or []
    if isinstance(mocks, str):
        mocks = [mocks]
    if not isinstance(mocks, list) or not mocks:
        return False
    ext = contract.get("external_services") or []
    if isinstance(ext, str):
        ext = [ext]
    if not isinstance(ext, list) or not ext:
        return True  # a mock list is declared; nothing specific to cover
    have = " ".join(str(m).lower() for m in mocks)
    return all(str(e).lower() in have for e in ext)


def _needs_mock(contract, files):
    """Why this contract needs a mock declaration, or None.
    Triggers: declared external_services / needs_network, a network
    command in test_command, or network imports/calls in the copied files."""
    if not isinstance(contract, dict):
        return None
    ext = contract.get("external_services")
    if isinstance(ext, list) and ext:
        return "contract declares external_services: %s" % ", ".join(str(e) for e in ext)
    if isinstance(ext, str) and ext.strip():
        return "contract declares external_services: %s" % ext.strip()
    if contract.get("needs_network") is True or contract.get("requires_network") is True:
        return "contract declares needs_network/requires_network"
    argv = _test_command(contract) or []
    for a in argv:
        if os.path.basename(str(a)) in _NET_CMDS:
            return "test_command invokes network tool %r" % os.path.basename(str(a))
        if "://" in str(a):
            return "test_command references a URL: %r" % str(a)
    for _f in (files or {}).values():
        try:
            with open(_f, encoding="utf-8", errors="ignore") as _fh:
                _text = _fh.read()
        except OSError:
            continue
        if _MOCK_NET_IMPORTS.search(_text or ""):
            return "network import/call in %s" % os.path.basename(str(_f))
    return None


def _check_mock_declaration(contract, files):
    """(verdict, detail): reject when an external-service dependency is
    detected but no mock is declared. (None, '') when no mock is needed
    or the declaration covers the dependency."""
    why = _needs_mock(contract, files)
    if why is None:
        return (None, "")
    if _mock_declaration_ok(contract):
        return (None, "")
    return ("reject", "external service dependency without mock declaration (%s); "
            "declare mock/mocks in the contract or remove the dependency" % why)


def _gate3_rerun(contract, src_files):
    """Gate 3: re-run the declared contract tests in the E4 sandbox.
    Cutover51 W5.1: the verifier re-runs itself and parses structured test
    counts. For code/script tasks a missing test_command is a REJECT (no
    more na); a green run that collected 0 tests is a REJECT."""
    argv = _test_command(contract)
    if argv is None:
        if isinstance(contract, dict) and contract.get("task_type") in ("code", "script"):
            return ("reject", "no test_command declared in contract (mandatory for code/script, cutover51)")
        return ("na", "no test_command declared in contract")
    files = {}
    for src in src_files:
        files[os.path.basename(src)] = src
    if isinstance(contract, dict):
        for tf in contract.get("test_files") or []:
            if isinstance(tf, str) and tf.strip():
                p = os.path.expanduser(tf.strip())
                if not os.path.isfile(p):
                    return ("reject", "declared test file missing: %s" % p)
                files[os.path.basename(p)] = p
    if not files:
        return ("na", "no source/test files to re-run")
    gm, gd = _check_mock_declaration(contract, files)
    if gm == "reject":
        return (gm, gd)
    timeout = contract.get("test_timeout_seconds") if isinstance(contract, dict) else None
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        timeout = _GATE_TEST_TIMEOUT_DEFAULT
    rc, out = _run_in_fresh_dir(argv, files, timeout)
    if isinstance(rc, str) and rc.startswith("sandbox-unavailable"):
        return ("reject", "test re-run refused: %s" % rc)
    if rc is None:
        return ("reject", "contract test re-run timed out (%ss)" % timeout)
    if rc != 0:
        return ("reject", "contract test re-run failed rc=%s: %s" % (rc, out.strip()[-300:]))
    counts = _parse_test_counts(out or "")
    if counts is not None and counts["passed"] == 0 and counts["collected"] in (0, None):
        return ("reject", "contract test re-run green but 0 tests collected/ran (cutover51)")
    if counts is not None and counts["failed"] > 0:
        return ("reject", "contract test re-run reports %d failed" % counts["failed"])
    return ("pass", "contract tests re-ran green in sandbox (passed=%s)" % (counts["passed"] if counts else "?"))


def _stub_source(path):
    """Stub version of a .py source: same top-level def/class names, all
    bodies replaced by pass. Unparseable -> empty file."""
    try:
        src = open(path, encoding="utf-8", errors="ignore").read()
        tree = _ast.parse(src, filename=path)
    except Exception:
        return ""
    lines = []
    for n in tree.body:
        if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            lines.append("def %s(*args, **kwargs):\n    pass\n" % n.name)
        elif isinstance(n, _ast.ClassDef):
            lines.append("class %s:\n    pass\n" % n.name)
    return "\n".join(lines)


def _gate4_redgreen(contract, src_files):
    """Gate 4: the same tests must FAIL on a stub and PASS on the real
    source. Only when test_command is declared and a .py source exists.
    Cutover51: missing test_command is a REJECT for code/script (no na)."""
    argv = _test_command(contract)
    if argv is None:
        if isinstance(contract, dict) and contract.get("task_type") in ("code", "script"):
            return ("reject", "no test_command declared in contract (mandatory for code/script, cutover51)")
        return ("na", "no test_command declared in contract")
    py_srcs = [s for s in src_files if s.endswith(".py")]
    if not py_srcs:
        return ("na", "no .py source for red-green")
    timeout = contract.get("test_timeout_seconds") if isinstance(contract, dict) else None
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        timeout = _GATE_TEST_TIMEOUT_DEFAULT
    test_files = {}
    if isinstance(contract, dict):
        for tf in contract.get("test_files") or []:
            if isinstance(tf, str) and tf.strip():
                p = os.path.expanduser(tf.strip())
                if not os.path.isfile(p):
                    return ("reject", "declared test file missing: %s" % p)
                test_files[os.path.basename(p)] = p
    _all4 = dict(test_files)
    for _s in py_srcs:
        _all4[os.path.basename(_s)] = _s
    gm, gd = _check_mock_declaration(contract, _all4)
    if gm == "reject":
        return (gm, gd)
    tmp = _tempfile.mkdtemp(prefix="c2v-gate4-")
    try:
        stubs = {}
        for src in py_srcs:
            sp = os.path.join(tmp, os.path.basename(src) + ".stub")
            with open(sp, "w", encoding="utf-8") as f:
                f.write(_stub_source(src))
            stubs[os.path.basename(src)] = sp
        files_stub = dict(test_files)
        files_stub.update(stubs)
        rc_stub, _ = _run_in_fresh_dir(argv, files_stub, timeout)
        if isinstance(rc_stub, str) and rc_stub.startswith("sandbox-unavailable"):
            return ("reject", "red-green stub run refused: %s" % rc_stub)
        if rc_stub == 0:
            return ("reject", "red-green: tests PASS on stub implementation (tests are vacuous)")
        if rc_stub is None:
            return ("reject", "red-green: stub run timed out (%ss)" % timeout)
        files_real = dict(test_files)
        for src in py_srcs:
            files_real[os.path.basename(src)] = src
        rc_real, out_real = _run_in_fresh_dir(argv, files_real, timeout)
        if isinstance(rc_real, str) and rc_real.startswith("sandbox-unavailable"):
            return ("reject", "red-green real-source run refused: %s" % rc_real)
        if rc_real is None:
            return ("reject", "red-green: real-source run timed out (%ss)" % timeout)
        if rc_real != 0:
            return ("reject", "red-green: tests FAIL on real source rc=%s: %s" % (rc_real, out_real.strip()[-300:]))
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)
    return ("pass", "tests fail on stub, pass on real source")


def _gate5_code(src_files, data):
    """Gate 5 (code): a deliverable that is only test output (pytest_exit=
    stub) with no source file anywhere is rejected. When a .py source
    exists, the artifact is the test evidence, not the deliverable."""
    if not src_files and data.strip().startswith(b"pytest_exit="):
        return ("reject", "test output only, no source file")
    return ("pass", "source file present" if src_files else "artifact is not test-output-only")


def _gate5_llm_echo(payload, data):
    """Gate 5 (llm_doc): deliverable identical to the contract input is an
    echo, not a deliverable. Returns (verdict, detail).
    NOTE (2026-11-13 hub-exec fix): compare against the CONTRACT INPUT (the
    work description), NOT payload["content"]. For pre-generated tasks the
    deliverable IS the pre-generated content by design, so comparing against
    it rejects every pre-generated task. The echo to catch is a worker that
    just copies the task description instead of producing the content."""
    inp_texts = []
    def _collect(v, _skip_content=False):
        if isinstance(v, str):
            inp_texts.append(v)
        elif isinstance(v, dict):
            for k, x in v.items():
                # 2026-11-13 gate5-fix: skip the "content" key. It holds the
                # pre-generated content itself; comparing the deliverable
                # against it rejects every pre-generated task (a2 + hw-n0).
                # The echo to catch is a worker copying the WORK DESCRIPTION
                # (input.work), not the pre-generated content.
                if _skip_content and k == "content":
                    continue
                _collect(x, _skip_content)
        elif isinstance(v, (list, tuple)):
            for x in v:
                _collect(x, _skip_content)
    if isinstance(payload, dict):
        _collect(payload.get("input"), _skip_content=True)
        contract = payload.get("contract")
        if isinstance(contract, dict):
            _collect(contract.get("input"), _skip_content=True)
    texts = [t for t in inp_texts if isinstance(t, str) and t.strip()]
    if not texts:
        return ("na", "no text input to compare")
    norm_data = _norm_ws(data)
    for t in texts:
        if norm_data and norm_data == _norm_ws(t):
            return ("reject", "llm_doc echo: deliverable equals contract input")
    return ("pass", "deliverable differs from input")


# Gate 6 (2026-11-13 factory repair): content quality for llm_doc.
# Tonight's N1 garbage (3,191B, task 55290397) passed gates 1+5 (file
# exists, non-empty, differs from input) while being a template: a single
# line with 59 literal \\n escapes, bracket placeholders ([文件路径],
# [行数], [任务 UUID], ...), 0 real table rows, no JSON copy. The verifier
# marked it DONE. Gate 6 rejects template/placeholder deliverables so
# they RETRY instead of DONE. Any one rule trips -> reject:
#   6a. known placeholder token inside [...] (not a Markdown link).
#   6b. >=5 non-link bracket groups (template residue).
#   6c. escaped single-line blob: >=10 literal \\n and <5 real newlines.
#   6d. assertion-type tasks (output_path/task_id mentions assert/断言):
#       fewer than 10 Markdown table data rows.
#   6e. assertion-type tasks: same-name .json copy missing next to .md.
_PLACEHOLDER_TOKENS = (
    "文件路径", "JSON 复制路径", "行数", "未核实数量", "任务 UUID",
    "声明时间", "完成时间", "未填", "待填", "待补充", "xxx", "TBD",
)
_ASSERT_ROW_RE = re.compile(
    r"^(A-[A-Z]+-\d+|K\d+|S\d+|M\d+|M-TOT|STOR-[A-Z]+\d*|STOR-[A-Z]+"
    r"|O\d+|B\d+|L\d+|C\d+|D\d+)$")

def _gate6_content_quality(payload, data, contract):
    """Gate 6 (llm_doc): content quality. Returns (verdict, detail)."""
    try:
        text = data.decode("utf-8", errors="ignore")
    except Exception:
        return ("na", "undecodable deliverable")
    # 6a. known placeholder tokens.
    for tok in _PLACEHOLDER_TOKENS:
        if ("[" + tok + "]") in text:
            return ("reject", "placeholder token [%s] in deliverable" % tok)
    # 6b. many non-link bracket groups = template residue.
    _brackets = [b for b in re.findall(r"\[[^\]\n]{1,30}\](?!\()", text)
                 if not re.match(r"^\[\d+\]$", b)]
    if len(_brackets) >= 5:
        return ("reject", "%d bracket placeholders in deliverable" % len(_brackets))
    # 6c. escaped single-line blob.
    if text.count("\\n") >= 10 and text.count("\n") < 5:
        return ("reject", "deliverable is escaped single-line blob (%d literal \\n, %d real newlines)"
                % (text.count("\\n"), text.count("\n")))
    # Assertion-type detection.
    _op = ""
    if isinstance(contract, dict):
        _op = str(contract.get("output_path") or "")
    _tid = ""
    if isinstance(payload, dict):
        _tid = str(payload.get("task_id") or "")
    _is_assert = ("assert" in _op.lower() or "assert" in _tid.lower()
                  or "断言" in _op or "断言" in _tid)
    if not _is_assert:
        return ("pass", "no placeholder; not an assertion task")
    # 6d. assertion table data rows.
    _rows = 0
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not cells:
            continue
        first = cells[0]
        if first in ("ID", "") or set(first) <= set("-: "):
            continue
        if _ASSERT_ROW_RE.match(first):
            _rows += 1
    if _rows < 10:
        return ("reject", "assertion table has %d data rows (<10)" % _rows)
    # 6e. same-name JSON copy next to the .md output.
    if _op:
        _md = _abs_hub_path(_op.strip())
        if _md.lower().endswith(".md"):
            _js = _md[:-3] + ".json"
            if not os.path.exists(_js):
                return ("reject", "assertion JSON copy missing: %s" % _js)
    return ("pass", "no placeholder; %d assertion rows; JSON copy present" % _rows)

def get_dsn():
    if not os.access(DSN_FILE, os.R_OK):
        return ""
    with open(DSN_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith("CQ_DSN="):
                return line[len("CQ_DSN="):].strip().strip('"').strip("'")
    return ""

def _eval_acceptance(cur, task_id, payload):
    """G9 (Eval OS phase 2, 2026-11-11): acceptance decision for type=eval.
    Returns (ok, reason). Fail-closed: there is no accepted eval acceptance
    source yet (no per-case independent_eval wiring, no trace writer), so a
    well-formed eval task still cannot auto-DONE; it is refused with an
    explicit reason instead of sailing through unchecked. A malformed eval
    task (missing eval_id/eval_run_id) is refused with its own reason.
    cur is unused for now (kept for the future PG-evidence lookup).
    """
    if not isinstance(payload, dict) or payload.get("type") != "eval":
        return False, "not an eval task"
    _eid = payload.get("eval_id")
    _erun = payload.get("eval_run_id")
    if not (isinstance(_eid, str) and _eid.strip() and isinstance(_erun, str) and _erun.strip()):
        return False, "eval task missing eval_id/eval_run_id"
    return False, "eval-no-acceptance-branch: no accepted eval acceptance source yet (independent_eval per-case wiring or M6 verdict pending); failing closed, no auto-DONE"


def main():
    dsn = get_dsn()
    if not dsn:
        print("c2-verifier: no DSN, refusing")
        return 0
    import psycopg
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT task_id, payload, current_fencing_token, artifact_sha, artifact_bytes, min_bytes, lane FROM central_tasks WHERE status='VERIFYING' ORDER BY created_at")
            rows = cur.fetchall()
            if not rows:
                print("c2-verifier: no verifying task")
                return 0
            for task_id, payload, token, sha, nbytes, min_bytes, lane in rows:
                cur.execute("SELECT fencing_token FROM central_leases WHERE task_id=%s AND status='ACTIVE'", (task_id,))
                lease = cur.fetchone()
                ok = True; reason = ""
                gates = {}
                if not lease or lease[0] != token:
                    ok = False; reason = "fencing mismatch"
                elif not sha or not SHA64.match(sha):
                    ok = False; reason = "sha not 64hex"
                elif nbytes is None or nbytes < (min_bytes or 0):
                    ok = False; reason = "bytes below min"
                elif payload.get("type") == "llm_doc":
                    path = os.path.join(DELIV_DIR, f"{task_id}.md")
                    if not os.path.exists(path):
                        ok = False; reason = "deliverable file missing"
                    else:
                        data = open(path, "rb").read()
                        if hashlib.sha256(data).hexdigest() != sha or len(data) != nbytes:
                            ok = False; reason = "file sha/bytes mismatch"
                        elif len(data) == 0:
                            ok = False; reason = "deliverable empty"
                        if ok:
                            # Five gates: 1 output_path, 5 echo. 2/3/4 are
                            # code-only and recorded as na.
                            _contract = payload.get("contract") if isinstance(payload, dict) else None
                            _cd = _contract if isinstance(_contract, dict) else {}
                            gates["2"] = ("na", "llm_doc: not code")
                            gates["3"] = ("na", "llm_doc: not code")
                            gates["4"] = ("na", "llm_doc: not code")
                            g1 = _gate1_output_paths(_cd)
                            gates["1"] = g1
                            if g1[0] == "reject":
                                ok = False; reason = "gate1: " + g1[1]
                            if ok:
                                g5 = _gate5_llm_echo(payload, data)
                                gates["5"] = g5
                                if g5[0] == "reject":
                                    ok = False; reason = "gate5: " + g5[1]
                            if ok:
                                g6 = _gate6_content_quality(payload, data, _cd)
                                gates["6"] = g6
                                if g6[0] == "reject":
                                    ok = False; reason = "gate6: " + g6[1]
                elif payload.get("type") == "patrol":
                    import time as _t2
                    path = "/home/ubuntu/ai-team/ceo-seat/out/patrol-latest.md"
                    if not os.path.exists(path):
                        ok = False; reason = "patrol-latest missing"
                    else:
                        data = open(path, "rb").read()
                        if hashlib.sha256(data).hexdigest() != sha or len(data) != nbytes:
                            ok = False; reason = "patrol file sha/bytes mismatch"
                        elif _t2.time() - os.path.getmtime(path) > 900:
                            ok = False; reason = "patrol-latest stale at verify"
                elif payload.get("type") == "code":
                    # 2026-11-06 anti-fake-DONE: code tasks must have a real
                    # deliverable file, not just a 156-byte pytest stub.
                    import glob as _g
                    _cands = _g.glob(os.path.join(DELIV_DIR, f"{task_id}*")) + _g.glob(os.path.join("/home/ubuntu/ai-team/state/c2-task-results", f"{task_id}*"))
                    if not _cands:
                        ok = False; reason = "code deliverable file missing"
                    else:
                        _data = open(_cands[0], "rb").read()
                        if hashlib.sha256(_data).hexdigest() != sha or len(_data) != nbytes:
                            ok = False; reason = "code file sha/bytes mismatch"
                        elif nbytes < 500:
                            ok = False; reason = "code artifact too small (<500B, likely pytest stub)"
                        elif _data.strip().startswith(b"pytest_exit=0") and len(_data) < 1000:
                            ok = False; reason = "code artifact is pytest stub only"
                        # Gates 2/3/4 (2026-11-06, verifier-gates-234).
                        # Gate 1 = file/sha/bytes (above); gate 5 = stub check (above).
                        # Gates 2/3/4 run only when 1+5 passed; anything else is NA.
                        _g = {}
                        if not ok:
                            if reason == "code artifact is pytest stub only":
                                _g["1"] = {"result": "PASS", "reason": "file/sha/bytes ok"}
                                _g["5"] = {"result": "FAIL", "reason": reason}
                            else:
                                _g["1"] = {"result": "FAIL", "reason": reason}
                                _g["5"] = {"result": "NA", "reason": "not evaluated"}
                            _g["2"] = {"result": "NA", "reason": "skipped: gate 1/5 failed"}
                            _g["3"] = {"result": "NA", "reason": "skipped: gate 1/5 failed"}
                            _g["4"] = {"result": "NA", "reason": "skipped: gate 1/5 failed"}
                        elif _GATES234 is None:
                            _g["1"] = {"result": "PASS", "reason": "file/sha/bytes ok"}
                            _g["5"] = {"result": "PASS", "reason": "not a pytest stub"}
                            _g["2"] = {"result": "FAIL", "reason": "gates234 module unavailable"}
                            _g["3"] = {"result": "NA", "reason": ""}
                            _g["4"] = {"result": "NA", "reason": ""}
                            ok = False; reason = "gates234 module unavailable"
                        else:
                            _g["1"] = {"result": "PASS", "reason": "file/sha/bytes ok"}
                            _g["5"] = {"result": "PASS", "reason": "not a pytest stub"}
                            try:
                                _contract = payload.get("contract") if isinstance(payload, dict) else None
                                _refdir = None
                                if isinstance(_contract, dict) and _contract.get("output_path"):
                                    _refdir = os.path.dirname(str(_contract["output_path"]))
                                    if not os.path.isdir(_refdir):
                                        _refdir = None
                                _require_bundle = isinstance(_contract, dict) and (_contract.get("format") == "PULLONCE-BUNDLE-V1" or bool(_contract.get("tests")) or bool(_contract.get("require_bundle")))
                                _g234 = _GATES234.check_code_gates(_data, reference_dir=_refdir, timeout_s=120, require_bundle=_require_bundle)
                                _g.update({"2": _g234["2"], "3": _g234["3"], "4": _g234["4"]})
                                if not _g234["ok"]:
                                    _bad = [k for k in ("2", "3", "4") if _g[k]["result"] == "FAIL"]
                                    ok = False
                                    reason = "gates234 FAIL: " + ",".join("%s(%s)" % (k, _g[k]["reason"][:80]) for k in _bad)
                            except Exception as _ge:
                                ok = False; reason = "gates234 error: %s" % _ge
                                _g["2"] = {"result": "FAIL", "reason": "gates234 error: %s" % _ge}
                                _g["3"] = {"result": "NA", "reason": ""}
                                _g["4"] = {"result": "NA", "reason": ""}
                        try:
                            os.makedirs(VERDICT_DIR, exist_ok=True)
                            with open(os.path.join(VERDICT_DIR, "%s.json" % task_id), "w", encoding="utf-8") as _vf:
                                json.dump({"task_id": str(task_id), "gates": _g, "ok": ok, "reason": reason}, _vf, ensure_ascii=False, indent=2)
                        except Exception:
                            pass
                        try:
                            _allv = sorted((os.path.join(VERDICT_DIR, _f) for _f in os.listdir(VERDICT_DIR) if _f.endswith(".json")), key=os.path.getmtime)
                            for _oldv in _allv[:-200]:
                                try:
                                    os.remove(_oldv)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        print("c2-verifier: GATES %s 1=%s 2=%s 3=%s 4=%s 5=%s" % (task_id, _g["1"]["result"], _g["2"]["result"], _g["3"]["result"], _g["4"]["result"], _g["5"]["result"]))
                        if ok:
                            # Thorsten Ball Rule 7 (2026-11-07): workdir isolation.
                            _contract = payload.get("contract") if isinstance(payload, dict) else None
                            _wd = payload.get("workdir")
                            if not _wd and isinstance(_contract, dict):
                                _wd = _contract.get("workdir")
                            if isinstance(_wd, str) and _wd.strip():
                                _conflict = _workdir_isolation_check(cur, task_id, _wd)
                                if _conflict:
                                    ok = False; reason = "workdir-shared with %s" % _conflict
                            # Thorsten Ball Rule 2 (2026-11-07): slop tripwire.
                            if ok:
                                _trips = slop_tripwire(_contract if isinstance(_contract, dict) else {}, _wd, _data)
                                if _trips:
                                    ok = False; reason = "slop: %s" % _trips[0]
                            # Thorsten Ball Rule 9 (2026-11-07): evidence markers.
                            if ok and not _evidence_markers_ok(_cands[0], _data):
                                ok = False; reason = "evidence-missing: artifact has no test result, sha256, or evidence marker"
                            # Thorsten Ball Rule 7 fix (2026-11-07): workdir must exist
                            # on the worker. Missing workdir = demerit; two consecutive
                            # misses for the same worker = REJECT.
                            if isinstance(_wd, str) and _wd.strip():
                                _exists = _workdir_exists_on_worker(_wd)
                                if _exists is False:
                                    _wid = None
                                    try:
                                        _wid = lease[0] if lease else None
                                    except Exception:
                                        _wid = None
                                    # worker_id is in the lease row; fall back to task lookup
                                    _worker = "worker-1"
                                    try:
                                        cur.execute("SELECT worker_id FROM central_leases WHERE task_id=%s AND status='ACTIVE' LIMIT 1", (task_id,))
                                        _lr = cur.fetchone()
                                        if _lr and _lr[0]:
                                            _worker = _lr[0]
                                    except Exception:
                                        pass
                                    _misses = _workdir_miss_record(_worker, True)
                                    print(f"c2-verifier: workdir-missing {task_id} worker={_worker} consecutive={_misses}", flush=True)
                                    if _misses >= 2:
                                        ok = False; reason = "workdir-missing-x2 (consecutive=%d)" % _misses
                                elif _exists is True:
                                    try:
                                        cur.execute("SELECT worker_id FROM central_leases WHERE task_id=%s AND status='ACTIVE' LIMIT 1", (task_id,))
                                        _lr = cur.fetchone()
                                        _worker = _lr[0] if _lr and _lr[0] else "worker-1"
                                    except Exception:
                                        _worker = "worker-1"
                                    _workdir_miss_record(_worker, False)
                        # Five gates (2026-11-07): 1 output_path, 2 AST,
                        # 3 re-run, 4 red-green, 5 source-present. Each
                        # records pass/reject/na; any reject fails the task.
                        if ok:
                            _contract = payload.get("contract") if isinstance(payload, dict) else None
                            _cd = _contract if isinstance(_contract, dict) else {}
                            _srcs = _py_source_candidates(_cd, _wd)
                            g1 = _gate1_output_paths(_cd)
                            gates["1"] = g1
                            if g1[0] == "reject":
                                ok = False; reason = "gate1: " + g1[1]
                            if ok:
                                g2 = _gate2_ast(_cd, _wd)
                                gates["2"] = g2
                                if g2[0] == "reject":
                                    ok = False; reason = "gate2: " + g2[1]
                            if ok:
                                g3 = _gate3_rerun(_cd, _srcs)
                                gates["3"] = g3
                                if g3[0] == "reject":
                                    ok = False; reason = "gate3: " + g3[1]
                            if ok:
                                g4 = _gate4_redgreen(_cd, _srcs)
                                gates["4"] = g4
                                if g4[0] == "reject":
                                    ok = False; reason = "gate4: " + g4[1]
                            if ok:
                                g5 = _gate5_code(_srcs, _data)
                                gates["5"] = g5
                                if g5[0] == "reject":
                                    ok = False; reason = "gate5: " + g5[1]
                            if ok:
                                # Cutover51 W5.1: forbidden-path check.
                                gf = _gate_forbidden(_cd, _srcs)
                                gates["F"] = gf
                                if gf[0] == "reject":
                                    ok = False; reason = "gateF: " + gf[1]
                elif payload.get("type") == "timer":
                    path = os.path.join("/home/ubuntu/ai-team/state/c2-task-results", f"{task_id}.txt")
                    if not os.path.exists(path):
                        ok = False; reason = "timer result file missing"
                    else:
                        data = open(path, "rb").read()
                        if hashlib.sha256(data).hexdigest() != sha or len(data) != nbytes:
                            ok = False; reason = "timer file sha/bytes mismatch"
                elif payload.get("type") == "eval":
                    # G9 (Eval OS phase 2, 2026-11-11): plug the no-branch
                    # straight-through DONE hole. An eval task used to pass
                    # on fencing+sha+bytes alone with zero eval judgement.
                    _eok, _ereason = _eval_acceptance(cur, task_id, payload)
                    gates["eval"] = ("pass", "eval acceptance ok") if _eok else ("reject", _ereason)
                    if not _eok:
                        ok = False; reason = _ereason
                # M5-2 (2026-11-05): cross-supplier review hook. ONLY llm_doc tasks
                # whose contract sets review_required=true go through the hook;
                # every other task keeps the exact old path below. Hook exits:
                #   0 = REVIEW PASS -> DONE; 1 = REVIEW FAIL -> RETRY_WAIT;
                #   2 = hook ERROR (no verdict) -> task STAYS VERIFYING, lease
                #   kept, next minute retries; after 3 error attempts -> RETRY_WAIT.
                # Kill-switch REVIEW-HOOK-DISABLED present -> hook skipped, bypass
                # logged, task proceeds to DONE (rollback path, same pattern as
                # GATE-DISABLED). Review can never override a deterministic failure.
                review_defer = False
                review_failed = False  # Method-07: review FAIL -> rework child (below)
                if ok and payload.get("type") == "llm_doc":
                    _contract = payload.get("contract") if isinstance(payload, dict) else None
                    if isinstance(_contract, dict) and _contract.get("review_required") is True:
                        import subprocess as _sp
                        if os.path.exists(REVIEW_HOOK_DISABLE):
                            # Cutover51 W2.2: kill-switch files are NEVER a
                            # bypass. Presence is an alarm: the review stays
                            # pending (defer, lease kept) and the file is
                            # recorded for the kill-switch monitor.
                            print(f"c2-verifier: review hook BLOCKED (kill-switch file present, no bypass) {task_id}")
                            try:
                                os.makedirs(REVIEW_DIR, exist_ok=True)
                                with open(os.path.join(REVIEW_DIR, "review-%s-killswitch.json" % task_id), "w", encoding="utf-8") as _bf:
                                    _bf.write(json.dumps({"task_id": str(task_id), "verdict": "BLOCKED", "reason": "REVIEW-HOOK-DISABLED present; cutover51: file bypass removed"}, ensure_ascii=False))
                            except Exception:
                                pass
                            review_defer = True
                        else:
                            try:
                                # Method-07: hook timeout 300s (strong review
                                # runs two reviewer calls, up to 120s each).
                                _hr = _sp.run([sys.executable, REVIEW_HOOK, "--task", str(task_id)], capture_output=True, text=True, timeout=300)
                                _hout = ((_hr.stdout or "") + " " + (_hr.stderr or "")).strip()
                                _hrc = _hr.returncode
                            except Exception as _he:
                                _hrc = 2; _hout = "hook subprocess error: %s" % _he
                            if _hrc == 0:
                                print(f"c2-verifier: review PASS {task_id} {_hout[:160]}")
                            elif _hrc == 1:
                                ok = False; reason = "review-FAIL"; review_failed = True
                                print(f"c2-verifier: review FAIL {task_id} {_hout[:200]}")
                            else:
                                try:
                                    _atts = len([n for n in os.listdir(REVIEW_DIR) if n.startswith("review-%s-" % task_id) and n.endswith(".json")])
                                except Exception:
                                    _atts = 0
                                if _atts >= 3:
                                    ok = False; reason = "review-hook-error-x3"
                                    print(f"c2-verifier: review hook ERROR x{_atts}, RETRY_WAIT {task_id}")
                                else:
                                    review_defer = True
                                    print(f"c2-verifier: review hook ERROR {task_id} attempt={_atts}/3, stays VERIFYING ({_hout[:160]})")
                # Five-gate receipt (acceptance criterion 4 of the five-gates
                # doc): every code/llm_doc verdict lists all five gates.
                if gates:
                    print("c2-verifier: GATES %s %s" % (
                        task_id, " ".join("%s=%s:%s" % (k, v[0], v[1]) for k, v in sorted(gates.items()))))
                if review_defer:
                    pass  # no status change, lease kept; next minute retries the review
                elif ok:
                    cur.execute("UPDATE central_tasks SET verified_by='c2-verifier', verified_at=now(), status='DONE' WHERE task_id=%s AND status='VERIFYING'", (task_id,))
                    print(f"c2-verifier: DONE {task_id}")
                else:
                    # F2 2026-11-10 (failure_id 11): retry>=max is terminal - QUARANTINED, no +1.
                    cur.execute("UPDATE central_tasks SET status=CASE WHEN retry_count>=COALESCE(max_retries,3) THEN 'QUARANTINED' ELSE 'RETRY_WAIT' END, retry_count=CASE WHEN retry_count>=COALESCE(max_retries,3) THEN retry_count ELSE retry_count+1 END WHERE task_id=%s AND status='VERIFYING' RETURNING status, retry_count", (task_id,))
                    _fr = cur.fetchone()
                    # F1 2026-11-10 (failure_id 12): stamp the just-created
                    # STATUS_CHANGE event row (diagnostic only; the audit
                    # trigger writes payload '{}').
                    _nst = _fr[0] if _fr else "RETRY_WAIT"
                    cur.execute(
                        "UPDATE central_events SET payload = jsonb_build_object('failure_class', %s::text, 'reason', %s::text)"
                        " WHERE event_id = (SELECT event_id FROM central_events"
                        " WHERE task_id=%s AND new_status=%s AND payload='{}'::jsonb"
                        " ORDER BY created_at DESC LIMIT 1)",
                        ("F-VERIFY", ("c2-verifier: " + str(reason))[:300], task_id, _nst))
                    print(f"c2-verifier: {(_fr[0] if _fr else 'RETRY_WAIT')} {task_id} reason={reason} retry={(_fr[1] if _fr else '?')}")
                if not review_defer:
                    cur.execute("UPDATE central_leases SET status='RELEASED' WHERE task_id=%s AND status='ACTIVE'", (task_id,))
                # Method-07: review FAIL generates a rework child (max 2 per
                # root); at the cap it writes an incident draft instead.
                # Only for review failures, never for deterministic failures.
                if review_failed and not review_defer:
                    try:
                        _create_rework_or_incident(cur, task_id, payload, lane, min_bytes)
                    except Exception as _re:
                        print(f"c2-verifier: rework creation failed parent={task_id}: {_re}")
            conn.commit()
    return 0

if __name__ == "__main__":
    sys.exit(main())
