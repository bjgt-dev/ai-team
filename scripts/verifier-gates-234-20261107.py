#!/usr/bin/env python3
"""verifier-gates-234-20261107.py -- verifier five-gates接线: gate 2/3/4 (code) + gate 1/5 (llm_doc).

Source of truth: ~/ai-team/state/verifier-five-gates-20261106.md (令19问1结构性封堵)
and ~/ai-team/docs/sops/code-dev-sop.md §五-1 (verifier封堵第2、3、4条未接).
Before this module, c2-verifier only enforced gate 1 (code, partial) and gate 5
(code pytest-stub only). Gates 2/3/4 and the llm_doc side of gates 1/5 existed
only in the SOP doc.

Gates implemented here (pure functions, no DB, stdlib only):
  gate 1: source file exists at the contract path. Contract output_path may
          contain a <uuid>/<task_id> placeholder (llm_doc template
          "state/deliverables/<uuid>.md"); it is resolved against task_id.
          Unresolvable / absent output_path -> NOT APPLICABLE (skip, never
          reject on a path we cannot resolve).
  gate 2: (code only) every .py in the deliverable parses with ast, and at
          least one non-test .py contains a non-empty function or class
          definition (body not only pass / ... / docstring).
  gate 3: (code only) re-run the contract tests in a fresh temp dir built
          from the deliverable itself; the worker's own pytest output is
          never trusted as the pass basis. No test files found -> N/A.
  gate 4: (code only) red-green check -- the same tests run against a stub
          implementation (every function body replaced by `return None`)
          MUST fail; if the stub passes, the tests are vacuous and the
          submission is rejected. No test files -> N/A.
  gate 5: (llm_doc) deliverable content equal (whitespace-normalised)
          to the contract input content is an echo, not a deliverable ->
          reject. No input content -> N/A. (code) a single-file artifact
          that is only pytest output (starts pytest_exit=0, <1000 bytes)
          is likewise rejected -- the same check c2-verifier already
          enforces inline; duplicated here so the module alone rejects
          all three regression samples.

Deliverable input: either a directory (source + tests) or a single file.
A single file may be a PULLONCE-BUNDLE-V1 bundle (first line
"PULLONCE-BUNDLE-V1", second line JSON manifest {files:[{path,bytes,sha256}]},
then a tar.gz of the files; bundle_sha256 is the sha256 of the tar.gz
bytes) -- it is unpacked in-memory and verified per embedded sha256
before any gate runs. A plain .py file is treated as a one-file
deliverable.

CLI:
  --artifact PATH --contract JSON [--task-id ID] [--timeout S] [--json]
Exit: 0 = every applicable gate PASS (or N/A); 1 = any gate REJECT;
      2 = tool error (unreadable artifact, bad contract JSON, bad bundle sha).
A tool error is NOT a rejection: callers (c2-verifier) must log it and
continue with the pre-existing checks, never flip DONE on a tool error and
never reject on one either.

Kill-switch (verifier wiring only): if
  /home/ubuntu/ai-team/state/VERIFIER-GATES234-DISABLED exists, c2-verifier
skips these gates. The file does not exist by default (gates active).
"""
import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

GATE_TIMEOUT_DEFAULT = 120


# ---------------------------------------------------------------- bundle
def unpack_bundle(data):
    """Return {path: bytes} if data is a PULLONCE-BUNDLE-V1 bundle, else None.

    Production format (state/pull-once-20261105/worker/pull_once.py
    _bundle_workdir): header line, manifest JSON line, then a tar.gz of
    the files; manifest bundle_sha256 is the sha256 of the tar.gz bytes.
    A legacy raw-concatenation layout (manifest files in order, no
    compression) is also accepted for old fixtures.
    Raises ValueError on a malformed bundle or a sha mismatch (tool error).
    """
    if not isinstance(data, (bytes, bytearray)):
        return None
    if not data.startswith(b"PULLONCE-BUNDLE-V1\n"):
        return None
    try:
        nl1 = data.index(b"\n", len(b"PULLONCE-BUNDLE-V1\n"))
    except ValueError:
        raise ValueError("bundle: manifest line missing")
    try:
        manifest = json.loads(data[len(b"PULLONCE-BUNDLE-V1\n"):nl1].decode("utf-8"))
    except Exception as exc:
        raise ValueError("bundle: manifest is not JSON: %s" % exc)
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError("bundle: manifest has no files list")
    payload = bytes(data[nl1 + 1:])
    expected = {e.get("path"): e for e in files if isinstance(e, dict)}
    if payload[:2] == b"\x1f\x8b":
        import gzip
        import io
        import tarfile
        if manifest.get("bundle_sha256") and \
                hashlib.sha256(payload).hexdigest() != manifest["bundle_sha256"]:
            raise ValueError("bundle: bundle_sha256 mismatch")
        try:
            raw = gzip.decompress(payload)
        except Exception as exc:
            raise ValueError("bundle: gzip decompress failed: %s" % exc)
        out = {}
        try:
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tf:
                for member in tf.getmembers():
                    if not member.isfile():
                        continue
                    f = tf.extractfile(member)
                    out[member.name] = f.read() if f is not None else b""
        except Exception as exc:
            raise ValueError("bundle: tar read failed: %s" % exc)
        for path, entry in expected.items():
            if path not in out:
                raise ValueError("bundle: manifest file missing from tar: %s" % path)
            sha = entry.get("sha256")
            if sha and hashlib.sha256(out[path]).hexdigest() != sha:
                raise ValueError("bundle: sha mismatch for %s" % path)
        return out
    off = 0
    out = {}
    for entry in files:
        path = entry.get("path")
        nbytes = entry.get("bytes")
        sha = entry.get("sha256")
        if not isinstance(path, str) or not isinstance(nbytes, int):
            raise ValueError("bundle: bad file entry %r" % (entry,))
        chunk = payload[off:off + nbytes]
        if len(chunk) != nbytes:
            raise ValueError("bundle: truncated file %s" % path)
        if sha and hashlib.sha256(chunk).hexdigest() != sha:
            raise ValueError("bundle: sha mismatch for %s" % path)
        out[path] = chunk
        off += nbytes
    return out


def load_artifact(artifact_path):
    """Return ({path: bytes}, kind) where kind is 'dir' | 'bundle' | 'file'.

    Raises OSError / ValueError (tool errors) on unreadable input.
    """
    if os.path.isdir(artifact_path):
        out = {}
        for root, dirs, names in os.walk(artifact_path):
            dirs[:] = [d for d in dirs if d not in
                       {"__pycache__", ".git", "node_modules", ".pytest_cache"}]
            for name in names:
                full = os.path.join(root, name)
                rel = os.path.relpath(full, artifact_path)
                with open(full, "rb") as f:
                    out[rel] = f.read()
        return out, "dir"
    with open(artifact_path, "rb") as f:
        data = f.read()
    files = unpack_bundle(data)
    if files is not None:
        return files, "bundle"
    return {os.path.basename(artifact_path): data}, "file"


# ---------------------------------------------------------------- helpers
def _is_test_path(path):
    base = os.path.basename(path)
    return base.startswith("test_") or base.endswith("_test.py") or "/tests/" in path.replace("\\", "/")


def _norm_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def _nonempty_def(node):
    """True if a FunctionDef/AsyncFunctionDef/ClassDef body does real work."""
    body = [s for s in node.body if not isinstance(s, ast.Expr) or
            not (isinstance(s.value, ast.Constant) and isinstance(s.value.value, str))]
    if not body:
        return False
    if len(body) == 1 and isinstance(body[0], ast.Pass):
        return False
    if len(body) == 1 and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and body[0].value.value is Ellipsis:
        return False
    return True


# ---------------------------------------------------------------- gate 1
def gate1_output_exists(contract, task_id):
    """(verdict, reason). verdict in PASS / REJECT / NA."""
    output_path = None
    if isinstance(contract, dict):
        output_path = contract.get("output_path")
        if not output_path and isinstance(contract.get("contract"), dict):
            output_path = contract["contract"].get("output_path")
    if not isinstance(output_path, str) or not output_path.strip():
        return "NA", "no output_path declared"
    path = output_path.strip()
    if task_id:
        path = path.replace("<uuid>", str(task_id)).replace("<task_id>", str(task_id))
    if "<uuid>" in path or "<task_id>" in path or "<" in path:
        return "NA", "output_path still has an unresolvable placeholder: %s" % path
    if not os.path.isabs(path):
        base = "/home/ubuntu/ai-team"
        path = os.path.join(base, path)
    if not os.path.isfile(path):
        return "REJECT", "gate1: contract output_path does not exist: %s" % path
    if os.path.getsize(path) == 0:
        return "REJECT", "gate1: contract output_path is empty: %s" % path
    return "PASS", "output_path exists (%d bytes): %s" % (os.path.getsize(path), path)


# ---------------------------------------------------------------- gate 2
def gate2_ast_nonempty(files):
    """(verdict, reason) over {path: bytes}."""
    py_files = {p: b for p, b in files.items()
                if p.endswith(".py") and not _is_test_path(p)}
    if not py_files:
        return "NA", "no non-test .py files in deliverable"
    for path in sorted(py_files):
        try:
            tree = ast.parse(py_files[path].decode("utf-8"))
        except Exception as exc:
            return "REJECT", "gate2: %s fails ast.parse: %s" % (path, exc)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if _nonempty_def(node):
                    return "PASS", "gate2: %s has a non-empty definition" % path
    return "REJECT", "gate2: no non-empty function/class definition in any .py (pass-only skeleton)"


# ---------------------------------------------------------------- gates 3/4
def _contract_test_names(contract, files):
    """Test file paths named by the contract, else discovered test_*.py."""
    names = []
    for src in (contract, contract.get("contract") if isinstance(contract, dict) else None):
        if isinstance(src, dict):
            tests = src.get("tests")
            if isinstance(tests, list):
                for t in tests:
                    if isinstance(t, str):
                        names.append(t)
                    elif isinstance(t, dict) and isinstance(t.get("path"), str):
                        names.append(t["path"])
    if names:
        return [n for n in names if n in files]
    return sorted(p for p in files if p.endswith(".py") and _is_test_path(p))


def _write_tree(files, dest):
    for path, data in files.items():
        full = os.path.join(dest, path)
        os.makedirs(os.path.dirname(full) or dest, exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)


def _stub_tree(files):
    """Copy of files where every non-test .py function body is `return None`."""
    out = dict(files)
    for path, data in files.items():
        if not path.endswith(".py") or _is_test_path(path):
            continue
        try:
            tree = ast.parse(data.decode("utf-8"))
        except Exception:
            continue
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                node.body = [ast.Return(value=ast.Constant(value=None))]
                node.decorator_list = []
                changed = True
            elif isinstance(node, ast.ClassDef):
                node.body = [ast.Pass()]
                node.decorator_list = []
                changed = True
        if changed:
            try:
                out[path] = ast.unparse(tree).encode("utf-8")
            except Exception:
                out[path] = b""
    return out


def _run_pytest(workdir, test_names, timeout):
    """(returncode, tail). returncode None on timeout/tool error."""
    cmd = [sys.executable, "-m", "pytest", "-q"] + test_names
    try:
        proc = subprocess.run(cmd, cwd=workdir, capture_output=True,
                              text=True, timeout=timeout)
        tail = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()[-1500:]
        return proc.returncode, tail
    except subprocess.TimeoutExpired:
        return None, "pytest timed out after %ss" % timeout
    except Exception as exc:
        return None, "pytest could not run: %s" % exc


def gate3_rerun(files, contract, timeout=GATE_TIMEOUT_DEFAULT):
    """(verdict, reason, detail). Re-runs contract tests in a fresh temp dir."""
    test_names = _contract_test_names(contract or {}, files)
    if not test_names:
        return "NA", "no contract test files in deliverable", ""
    with tempfile.TemporaryDirectory(prefix="gates234-rerun-") as tmp:
        _write_tree(files, tmp)
        rc, tail = _run_pytest(tmp, test_names, timeout)
    if rc is None:
        return "REJECT", "gate3: re-run of %s did not complete (%s)" % (test_names, tail[:200]), tail
    if rc != 0:
        return "REJECT", "gate3: re-run failed (exit %d) for %s" % (rc, test_names), tail
    return "PASS", "gate3: re-run passed (exit 0) for %s" % (test_names,), tail


def gate4_redgreen(files, contract, timeout=GATE_TIMEOUT_DEFAULT):
    """(verdict, reason, detail). Same tests must FAIL against a stub impl."""
    test_names = _contract_test_names(contract or {}, files)
    if not test_names:
        return "NA", "no contract test files in deliverable", ""
    if not any(p.endswith(".py") and not _is_test_path(p) for p in files):
        return "NA", "no non-test .py files to stub", ""
    stub = _stub_tree(files)
    with tempfile.TemporaryDirectory(prefix="gates234-stub-") as tmp:
        _write_tree(stub, tmp)
        rc, tail = _run_pytest(tmp, test_names, timeout)
    if rc is None:
        return "PASS", "gate4: stub run did not complete (%s); stub did not pass" % tail[:160], tail
    if rc == 0:
        return "REJECT", "gate4: stub implementation PASSED the tests -- tests are vacuous (red-green check failed)", tail
    return "PASS", "gate4: stub correctly FAILED the tests (exit %d)" % rc, tail


# ---------------------------------------------------------------- gate 5
def gate5_pytest_stub(data):
    """(verdict, reason). Code artifact that is only pytest output."""
    if isinstance(data, (bytes, bytearray)):
        text = bytes(data)
        if text.strip().startswith(b"pytest_exit=0") and len(text) < 1000:
            return "REJECT", "gate5: artifact is pytest output only (starts pytest_exit=0, %d bytes)" % len(text)
    return "PASS", "not a pytest-only stub"


def gate5_no_echo(deliverable_text, contract):
    """(verdict, reason). llm_doc deliverable == contract input content."""
    content = None
    if isinstance(contract, dict):
        inp = contract.get("input")
        if isinstance(inp, dict):
            content = inp.get("content")
        elif isinstance(inp, str):
            content = inp
        if content is None and isinstance(contract.get("contract"), dict):
            content = contract["contract"].get("input")
    if not isinstance(content, str) or not _norm_text(content):
        return "NA", "no contract input content to compare"
    if not isinstance(deliverable_text, str):
        return "NA", "deliverable is not text"
    if _norm_text(deliverable_text) == _norm_text(content):
        return "REJECT", "gate5: deliverable is identical to contract input (llm_doc echo, not a deliverable)"
    return "PASS", "deliverable differs from contract input"


# ---------------------------------------------------------------- orchestrate
def check_artifact(artifact_path, contract, task_id=None, timeout=GATE_TIMEOUT_DEFAULT):
    """Run all applicable gates. Returns list of (gate, verdict, reason).

    Raises OSError / ValueError on tool errors (unreadable artifact, bad
    bundle). Callers must treat an exception as "tool error, skip", never
    as a pass or a reject.
    """
    files, kind = load_artifact(artifact_path)
    task_type = (contract or {}).get("task_type")
    results = []
    v, r = gate1_output_exists(contract or {}, task_id)
    results.append(("gate1", v, r))
    if task_type == "code":
        if kind == "file":
            raw = next(iter(files.values()))
            v, r = gate5_pytest_stub(raw)
            results.append(("gate5", v, r))
        v, r = gate2_ast_nonempty(files)
        results.append(("gate2", v, r))
        v, r, _d = gate3_rerun(files, contract or {}, timeout)
        results.append(("gate3", v, r))
        v, r, _d = gate4_redgreen(files, contract or {}, timeout)
        results.append(("gate4", v, r))
    elif task_type == "llm_doc":
        text = None
        for path in sorted(files):
            if path.endswith(".md"):
                text = files[path].decode("utf-8", errors="ignore")
                break
        if text is None and files:
            text = next(iter(files.values())).decode("utf-8", errors="ignore")
        v, r = gate5_no_echo(text if text is not None else "", contract or {})
        results.append(("gate5", v, r))
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description="verifier five-gates 2/3/4 + llm_doc 1/5 checker")
    ap.add_argument("--artifact", required=True, help="deliverable file/dir (bundle supported)")
    ap.add_argument("--contract", required=True, help="contract JSON file")
    ap.add_argument("--task-id", default=None)
    ap.add_argument("--timeout", type=int, default=GATE_TIMEOUT_DEFAULT)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        with open(args.contract, encoding="utf-8") as f:
            contract = json.load(f)
    except Exception as exc:
        print("TOOL-ERROR: cannot read contract %s: %s" % (args.contract, exc))
        return 2
    if not isinstance(contract, dict):
        print("TOOL-ERROR: contract is not a JSON object")
        return 2
    try:
        results = check_artifact(args.artifact, contract, args.task_id, args.timeout)
    except (OSError, ValueError) as exc:
        print("TOOL-ERROR: %s" % exc)
        return 2
    rejected = [r for r in results if r[1] == "REJECT"]
    if args.json:
        print(json.dumps([{"gate": g, "verdict": v, "reason": r} for g, v, r in results],
                         ensure_ascii=False, indent=2))
    else:
        for g, v, r in results:
            print("%s: %s -- %s" % (g, v, r))
    return 1 if rejected else 0


if __name__ == "__main__":
    sys.exit(main())
