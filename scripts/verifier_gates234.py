import json
import gzip
import tarfile
import io
import os
import hashlib
import ast
import re
import subprocess
import tempfile
import sys
import shutil
import time

BUNDLE_MAGIC = b"PULLONCE-BUNDLE-V1\n"
DEFAULT_TIMEOUT_S = 120

# M0-A (2026-11-25): per-gate timing of the last gate3/gate4 pytest run.
# gate3/gate4 keep their (result, reason) return signature; they stash
# measurements here and check_code_gates reads them into durations_ms /
# pytest_seconds. Cleared at the top of every check_code_gates call so a
# skipped gate never reports a stale previous run.
_LAST_RUN = {}


def _parse_pytest_seconds(text):
    """Last 'in Ns' duration in a pytest-style summary, or None.
    (Mirrors c2-verifier-20261104._parse_pytest_seconds.)"""
    ms = re.findall(r"in\s+(\d+(?:\.\d+)?)s\b", text or "")
    if not ms:
        return None
    try:
        return float(ms[-1])
    except ValueError:
        return None

def is_test_file(name):
    return name.startswith("test_") or name.endswith("_test.py")

def _is_empty_stmt(node):
    if isinstance(node, ast.Pass):
        return True
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, (str, type(Ellipsis))):
        return True
    return False

def _is_nonempty_def(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(not _is_empty_stmt(n) for n in node.body):
        return True
    if isinstance(node, ast.ClassDef) and (any(_is_nonempty_def(n) for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))) or any(not _is_empty_stmt(n) for n in node.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))):
        return True
    return False

def nonempty_defs(tree):
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_nonempty_def(node):
            out.append(("function", node.name))
        elif isinstance(node, ast.ClassDef) and _is_nonempty_def(node):
            out.append(("class", node.name))
    return out

def parse_bundle(data: bytes):
    """Parse a PULLONCE-BUNDLE-V1 artifact. Fail-closed (butler C3):
    ANY parse problem raises ValueError; nothing escapes to the caller."""
    if not data.startswith(BUNDLE_MAGIC):
        return None
    try:
        first_nl = data.index(b"\n", len(BUNDLE_MAGIC))
        manifest = json.loads(data[len(BUNDLE_MAGIC):first_nl])
        if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
            raise ValueError("manifest is not a dict with a files list")
        gz_bytes = data[first_nl + 1:]
        tar_bytes = gzip.decompress(gz_bytes)
        tar = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r")
        want = {e["path"]: e for e in manifest["files"]}
        files = {}
        for member in tar.getmembers():
            if not member.isfile():
                continue
            name = member.name
            if name not in want:
                raise ValueError("Missing manifest entry for " + name)
            fobj = tar.extractfile(member)
            if fobj is None:
                raise ValueError("Cannot extract " + name)
            content = fobj.read()
            if hashlib.sha256(content).hexdigest() != want[name]["sha256"]:
                raise ValueError("SHA256 mismatch for " + name)
            files[name] = content
        tar.close()
        return {"files": files, "manifest": manifest}
    except (json.JSONDecodeError, gzip.BadGzipFile, tarfile.TarError,
            OSError, KeyError, UnicodeDecodeError, AttributeError,
            ValueError) as e:
        raise ValueError("Corrupt bundle: " + str(e))

def build_bundle_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name in sorted(files):
            data = files[name]
            info = tarfile.TarInfo(name); info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    tar_bytes = buf.getvalue()
    gz_bytes = gzip.compress(tar_bytes)
    manifest = {"format": "PULLONCE-BUNDLE-V1", "files": [{"path": n, "bytes": len(files[n]), "sha256": hashlib.sha256(files[n]).hexdigest()} for n in sorted(files)], "bundle_bytes": len(gz_bytes), "bundle_sha256": hashlib.sha256(gz_bytes).hexdigest()}
    return BUNDLE_MAGIC + json.dumps(manifest, sort_keys=True).encode() + b"\n" + gz_bytes

def gate2_ast_check(files: dict[str, bytes]) -> tuple:
    non_test_files = [name for name in files if not is_test_file(name) and name.endswith(".py")]
    if not non_test_files:
        return ("FAIL", "no python source in bundle")
    for name in non_test_files:
        try:
            tree = ast.parse(files[name])
        except SyntaxError as e:
            return ("FAIL", "ast parse failed: " + name + ": " + str(e))
    trees = {}
    for name in non_test_files:
        trees[name] = ast.parse(files[name])
    non_empty_defs_count = sum(len(nonempty_defs(trees[name])) for name in non_test_files)
    if non_empty_defs_count == 0:
        return ("FAIL", "no non-empty function/class (import-only or pass skeleton)")
    return ("PASS", "ast ok, " + str(non_empty_defs_count) + " non-empty defs")

SANDBOX_BASE = "/var/tmp"
SANDBOX_CPU_S = 60
SANDBOX_AS_BYTES = 1073741824
SANDBOX_FSIZE_BYTES = 104857600
SANDBOX_NPROC = 64


def _make_workdir(files):
    """Create a sandbox workdir under SANDBOX_BASE, world-readable so the
    unprivileged sandbox user can read the bundle files (butler C2)."""
    tmpdir = tempfile.mkdtemp(prefix="gates234-", dir=SANDBOX_BASE)
    os.chmod(tmpdir, 0o755)
    for name, data in files.items():
        p = os.path.join(tmpdir, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
        os.chmod(p, 0o644)
    return tmpdir


def _cleanup_workdir(tmpdir):
    """Remove a sandbox workdir. Files created by the sandbox user (nobody)
    cannot be removed by the verifier user; fall back to sudo rm."""
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass
    if os.path.exists(tmpdir):
        try:
            subprocess.run(["sudo", "-n", "rm", "-rf", tmpdir],
                           capture_output=True, timeout=30)
        except Exception:
            pass


def _run_pytest(tmpdir, timeout_s):
    """Run pytest sandboxed (butler C2): sudo unshare -n (no network) +
    setpriv nobody + clean env + prlimit (cpu/as/fsize/nproc) + timeout.
    Fail-closed: if the sandbox itself is unavailable, return None (the
    gate FAILs) instead of running unsandboxed.
    Returns (returncode, tail, elapsed_ms); returncode None means the
    sandbox was unavailable or the run timed out."""
    _t0 = time.perf_counter()
    probe = subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=15)
    if probe.returncode != 0:
        return (None, "sandbox unavailable: sudo -n failed",
                (time.perf_counter() - _t0) * 1000.0)
    cmd = ["sudo", "unshare", "-n", "setpriv", "--reuid=nobody",
           "--regid=nogroup", "--clear-groups",
           "env", "-i", "PATH=/usr/bin:/bin", "HOME=" + str(tmpdir),
           "PYTHONDONTWRITEBYTECODE=1",
           "prlimit", "--cpu=%d" % SANDBOX_CPU_S,
           "--as=%d" % SANDBOX_AS_BYTES,
           "--fsize=%d" % SANDBOX_FSIZE_BYTES,
           "--nproc=%d" % SANDBOX_NPROC,
           "python3", "-m", "pytest", "-q"]
    try:
        result = subprocess.run(cmd, cwd=str(tmpdir), capture_output=True, text=True, timeout=timeout_s)
        return (result.returncode, (result.stdout + result.stderr)[-500:],
                (time.perf_counter() - _t0) * 1000.0)
    except subprocess.TimeoutExpired:
        return (None, "pytest timeout after %ss" % timeout_s,
                (time.perf_counter() - _t0) * 1000.0)
    except FileNotFoundError as e:
        return (None, "sandbox unavailable: " + str(e),
                (time.perf_counter() - _t0) * 1000.0)

def gate3_rerun_tests(files: dict[str, bytes], reference_dir=None, timeout_s=120) -> tuple:
    if not any(is_test_file(name) for name in files):
        return ("NA", "no test files in bundle")
    if reference_dir:
        for name in files:
            if not is_test_file(name):
                continue
            ref_path = os.path.join(reference_dir, name)
            if not os.path.isfile(ref_path):
                return ("FAIL", "test file " + name + " missing from contract reference")
            if hashlib.sha256(open(ref_path, "rb").read()).hexdigest() != hashlib.sha256(files[name]).hexdigest():
                return ("FAIL", "test file " + name + " sha differs from contract reference")
    tmpdir = _make_workdir(files)
    try:
        returncode, tail, elapsed_ms = _run_pytest(tmpdir, timeout_s)
        _LAST_RUN["3"] = {"duration_ms": round(elapsed_ms, 1),
                          "pytest_seconds": _parse_pytest_seconds(tail)}
        if returncode == 0:
            return ("PASS", "pytest exit 0 in fresh dir")
        elif returncode is None:
            return ("FAIL", tail if tail else "pytest timeout after %ss" % timeout_s)
        else:
            return ("FAIL", "pytest exit %s: %s" % (returncode, tail))
    finally:
        _cleanup_workdir(tmpdir)

def _build_stub_files(files: dict[str, bytes]) -> dict[str, bytes]:
    stubs = {}
    for name, data in files.items():
        if not is_test_file(name) and name.endswith(".py"):
            tree = ast.parse(data)
            stub = []
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                    stub.append("def %s(*args, **kwargs):\n    return None\n" % node.name)
                elif isinstance(node, ast.ClassDef):
                    stub.append("class %s:\n    pass\n" % node.name)
            if not stub:
                stub.append("# stub: no defs\n")
            stub = "".join(stub)
            stubs[name] = stub.encode()
        else:
            stubs[name] = data
    return stubs

def gate4_red_green(files: dict[str, bytes], real_passed: bool, timeout_s=120) -> tuple:
    if not real_passed:
        return ("NA", "skipped: gate 3 did not pass")
    stubs = _build_stub_files(files)
    merged = dict(stubs)
    for name, data in files.items():
        if is_test_file(name):
            merged[name] = data
    tmpdir = _make_workdir(merged)
    try:
        returncode, tail, elapsed_ms = _run_pytest(tmpdir, timeout_s)
        # Gate 4 only runs the stub (the real-source pass is already
        # established by gate 3); duration/pytest seconds are the stub run's.
        _LAST_RUN["4"] = {"duration_ms": round(elapsed_ms, 1),
                          "pytest_seconds": _parse_pytest_seconds(tail)}
        if returncode is None:
            return ("FAIL", tail if tail else "stub pytest timeout")
        if returncode == 0:
            return ("FAIL", "tests pass against stub: vacuous tests")
        return ("PASS", "stub run failed as expected (exit %s)" % returncode)
    finally:
        _cleanup_workdir(tmpdir)

def check_code_gates(data: bytes, reference_dir=None, timeout_s=120, require_bundle=False) -> dict:
    """Run gates 2/3/4 once. Returned dict (backward compatible: old
    callers only read "2"/"3"/"4"/"ok") gains:
      durations_ms: {"2"|"3"|"4": wall ms of this call (0.0 when not run)},
      pytest_seconds: {"3"|"4": pytest-reported seconds or None},
      runs: 1 (this dict covers a single run; the caller loops for pass^3).
    Timing lives in this module; the loop lives in the caller, so there is
    exactly one loop, not one per layer."""
    _LAST_RUN.clear()
    _zero = {"durations_ms": {"2": 0.0, "3": 0.0, "4": 0.0},
             "pytest_seconds": {"3": None, "4": None}, "runs": 1}
    try:
        bundle = parse_bundle(data)
    except Exception as e:
        return {"2": {"result": "FAIL", "reason": "corrupt bundle: " + str(e)}, "3": {"result": "NA", "reason": "no tests in legacy artifact"}, "4": {"result": "NA", "reason": "no tests in legacy artifact"}, "ok": False, **_zero}
    if bundle:
        _t2 = time.perf_counter()
        result2, reason2 = gate2_ast_check(bundle["files"])
        _d2 = round((time.perf_counter() - _t2) * 1000.0, 1)
        result3, reason3 = gate3_rerun_tests(bundle["files"], reference_dir, timeout_s)
        _d3 = _LAST_RUN.get("3", {}).get("duration_ms", 0.0)
        _s3 = _LAST_RUN.get("3", {}).get("pytest_seconds")
        result4, reason4 = gate4_red_green(bundle["files"], result3 == "PASS")
        _d4 = _LAST_RUN.get("4", {}).get("duration_ms", 0.0)
        _s4 = _LAST_RUN.get("4", {}).get("pytest_seconds")
        return {"2": {"result": result2, "reason": reason2}, "3": {"result": result3, "reason": reason3}, "4": {"result": result4, "reason": reason4}, "ok": result2 != "FAIL" and result3 != "FAIL" and result4 != "FAIL",
                "durations_ms": {"2": _d2, "3": _d3, "4": _d4},
                "pytest_seconds": {"3": _s3, "4": _s4}, "runs": 1}
    else:
        if require_bundle:
            return {"2": {"result": "FAIL", "reason": "legacy artifact: contract requires PULLONCE-BUNDLE-V1"}, "3": {"result": "NA", "reason": "no tests in legacy artifact"}, "4": {"result": "NA", "reason": "no tests in legacy artifact"}, "ok": False, **_zero}
        try:
            tree = ast.parse(data)
        except (SyntaxError, UnicodeDecodeError):
            return {"2": {"result": "FAIL", "reason": "artifact is neither a bundle nor parseable python"}, "3": {"result": "NA", "reason": "no tests in legacy artifact"}, "4": {"result": "NA", "reason": "no tests in legacy artifact"}, "ok": False, **_zero}
        _t2 = time.perf_counter()
        result2, reason2 = gate2_ast_check({"legacy.py": data})
        _d2 = round((time.perf_counter() - _t2) * 1000.0, 1)
        result3, reason3 = ("NA", "no tests in legacy artifact")
        result4, reason4 = ("NA", "no tests in legacy artifact")
        return {"2": {"result": result2, "reason": reason2}, "3": {"result": result3, "reason": reason3}, "4": {"result": result4, "reason": reason4}, "ok": result2 != "FAIL" and result3 != "FAIL" and result4 != "FAIL",
                "durations_ms": {"2": _d2, "3": 0.0, "4": 0.0},
                "pytest_seconds": {"3": None, "4": None}, "runs": 1}
