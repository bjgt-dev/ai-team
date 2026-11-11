import json
import gzip
import tarfile
import io
import os
import hashlib
import ast
import subprocess
import tempfile
import sys
import shutil

BUNDLE_MAGIC = b"PULLONCE-BUNDLE-V1\n"
DEFAULT_TIMEOUT_S = 120

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
    gate FAILs) instead of running unsandboxed."""
    probe = subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=15)
    if probe.returncode != 0:
        return (None, "sandbox unavailable: sudo -n failed")
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
        return (result.returncode, (result.stdout + result.stderr)[-500:])
    except subprocess.TimeoutExpired:
        return (None, "pytest timeout after %ss" % timeout_s)
    except FileNotFoundError as e:
        return (None, "sandbox unavailable: " + str(e))

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
        returncode, tail = _run_pytest(tmpdir, timeout_s)
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
        returncode, tail = _run_pytest(tmpdir, timeout_s)
        if returncode is None:
            return ("FAIL", tail if tail else "stub pytest timeout")
        if returncode == 0:
            return ("FAIL", "tests pass against stub: vacuous tests")
        return ("PASS", "stub run failed as expected (exit %s)" % returncode)
    finally:
        _cleanup_workdir(tmpdir)

def check_code_gates(data: bytes, reference_dir=None, timeout_s=120, require_bundle=False) -> dict:
    try:
        bundle = parse_bundle(data)
    except Exception as e:
        return {"2": {"result": "FAIL", "reason": "corrupt bundle: " + str(e)}, "3": {"result": "NA", "reason": "no tests in legacy artifact"}, "4": {"result": "NA", "reason": "no tests in legacy artifact"}, "ok": False}
    if bundle:
        result2, reason2 = gate2_ast_check(bundle["files"])
        result3, reason3 = gate3_rerun_tests(bundle["files"], reference_dir, timeout_s)
        result4, reason4 = gate4_red_green(bundle["files"], result3 == "PASS")
        return {"2": {"result": result2, "reason": reason2}, "3": {"result": result3, "reason": reason3}, "4": {"result": result4, "reason": reason4}, "ok": result2 != "FAIL" and result3 != "FAIL" and result4 != "FAIL"}
    else:
        if require_bundle:
            return {"2": {"result": "FAIL", "reason": "legacy artifact: contract requires PULLONCE-BUNDLE-V1"}, "3": {"result": "NA", "reason": "no tests in legacy artifact"}, "4": {"result": "NA", "reason": "no tests in legacy artifact"}, "ok": False}
        try:
            tree = ast.parse(data)
        except (SyntaxError, UnicodeDecodeError):
            return {"2": {"result": "FAIL", "reason": "artifact is neither a bundle nor parseable python"}, "3": {"result": "NA", "reason": "no tests in legacy artifact"}, "4": {"result": "NA", "reason": "no tests in legacy artifact"}, "ok": False}
        result2, reason2 = gate2_ast_check({"legacy.py": data})
        result3, reason3 = ("NA", "no tests in legacy artifact")
        result4, reason4 = ("NA", "no tests in legacy artifact")
        return {"2": {"result": result2, "reason": reason2}, "3": {"result": result3, "reason": reason3}, "4": {"result": result4, "reason": reason4}, "ok": result2 != "FAIL" and result3 != "FAIL" and result4 != "FAIL"}
