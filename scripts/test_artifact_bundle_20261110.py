#!/usr/bin/env python3
"""test_artifact_bundle_20261110.py -- regression tests for the review-409 fix.

Covers the canonical artifact parser (scripts/artifact_bundle.py) and the
review extraction path (queue_api._extract_artifact_files):

  bundle round-trip, producer-realistic manifest (pytest extra field),
  plain tar, plain .py, plain text, corrupt bundles (bad sha / bad
  manifest / truncated gzip / missing file), cross-check against the
  verifier's unpack_bundle (same vectors must agree), extraction via the
  queue_api path (bundle + tar accepted, plain file rejected), and tar
  path-traversal safety.

Exit 0 = all PASS; any FAIL exits 1. No network, no DB, no gateway calls.
"""
import hashlib
import importlib.util
import io
import json
import os
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ab = _load("artifact_bundle", os.path.join(HERE, "artifact_bundle.py"))
vg = _load("verifier_gates234_20261107",
           os.path.join(HERE, "verifier-gates-234-20261107.py"))
qa = _load("queue_api_20261105",
           os.path.join(HERE, "queue_api-20261105.py"))

FILES = {
    "add_one.py": b"def add_one(n):\n    return n + 1\n",
    "test_add_one.py": (
        b"from add_one import add_one\n\n"
        b"def test_zero():\n    assert add_one(0) == 1\n\n"
        b"def test_neg():\n    assert add_one(-5) == -4\n"),
}
PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS %s" % name)
    else:
        FAIL += 1
        print("FAIL %s %s" % (name, detail))


def expect_raises(name, fn, exc_types=(ValueError,)):
    try:
        fn()
    except exc_types as e:
        check(name, True)
        return str(e)
    except Exception as e:
        check(name, False, "wrong exception %r" % e)
        return ""
    check(name, False, "no exception raised")
    return ""


def main():
    # T1: bundle round-trip
    blob = ab.build_bundle(FILES)
    got, kind = ab.load_artifact(blob)
    check("T1 bundle round-trip", kind == "bundle" and got == FILES,
          "kind=%s keys=%s" % (kind, sorted(got) if isinstance(got, dict) else got))

    # T2: producer-realistic manifest (pytest extra field, as pull_once emits)
    blob2 = ab.build_bundle(FILES, extra={"pytest": "pytest_exit=0\nworkdir=x\n"})
    got2, kind2 = ab.load_artifact(blob2)
    check("T2 bundle with pytest extra field", kind2 == "bundle" and got2 == FILES,
          "kind=%s" % kind2)

    # T3: plain tar -> kind tar
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name in sorted(FILES):
            info = tarfile.TarInfo(name)
            info.size = len(FILES[name])
            tf.addfile(info, io.BytesIO(FILES[name]))
    got3, kind3 = ab.load_artifact(buf.getvalue())
    check("T3 plain tar", kind3 == "tar" and got3 == FILES, "kind=%s" % kind3)

    # T4: plain .py -> kind file
    got4, kind4 = ab.load_artifact(FILES["add_one.py"])
    check("T4 plain .py file", kind4 == "file" and got4 == {"artifact": FILES["add_one.py"]},
          "kind=%s keys=%s" % (kind4, list(got4)))

    # T5: plain text (pytest output, no bundleable files) -> kind file
    txt = b"pytest_exit=0\nworkdir=/x\n2 passed\n"
    got5, kind5 = ab.load_artifact(txt)
    check("T5 plain pytest text", kind5 == "file", "kind=%s" % kind5)

    # T6: corrupt bundle -- bad bundle_sha256
    bad = bytearray(blob)
    # flip a byte inside the gzip payload (after the 2nd newline)
    nl2 = blob.index(b"\n", blob.index(b"\n") + 1)
    bad[nl2 + 5] ^= 0xFF
    expect_raises("T6 corrupt bundle bad sha", lambda: ab.parse_bundle(bytes(bad)))

    # T7: corrupt bundle -- manifest not JSON
    bad7 = b"PULLONCE-BUNDLE-V1\nnot-json\n" + blob[blob.index(b"\n", blob.index(b"\n") + 1) + 1:]
    expect_raises("T7 corrupt bundle bad manifest", lambda: ab.parse_bundle(bad7))

    # T8: corrupt bundle -- truncated gzip payload
    bad8 = blob[:len(blob) - 20]
    expect_raises("T8 corrupt bundle truncated gzip", lambda: ab.parse_bundle(bad8))

    # T9: corrupt bundle -- manifest file missing from tar
    manifest = {"format": "PULLONCE-BUNDLE-V1",
                "files": [{"path": "add_one.py", "bytes": len(FILES["add_one.py"]),
                           "sha256": hashlib.sha256(FILES["add_one.py"]).hexdigest()},
                          {"path": "ghost.py", "bytes": 3,
                           "sha256": hashlib.sha256(b"xxx").hexdigest()}],
                "bundle_bytes": 0, "bundle_sha256": ""}
    buf9 = io.BytesIO()
    with tarfile.open(fileobj=buf9, mode="w:gz") as tf:
        info = tarfile.TarInfo("add_one.py")
        info.size = len(FILES["add_one.py"])
        tf.addfile(info, io.BytesIO(FILES["add_one.py"]))
    gz9 = buf9.getvalue()
    manifest["bundle_bytes"] = len(gz9)
    manifest["bundle_sha256"] = hashlib.sha256(gz9).hexdigest()
    blob9 = (b"PULLONCE-BUNDLE-V1\n" + json.dumps(manifest, sort_keys=True).encode()
             + b"\n" + gz9)
    expect_raises("T9 corrupt bundle missing file", lambda: ab.parse_bundle(blob9))

    # T10: cross-check with the verifier's unpack_bundle (same vector, same result)
    v_out = vg.unpack_bundle(blob2)
    check("T10 verifier unpack_bundle agrees", v_out == FILES,
          "verifier keys=%s" % sorted(v_out) if isinstance(v_out, dict) else v_out)

    # T11-T15: queue_api extraction path (no LLM producer involved)
    tmpd = tempfile.mkdtemp(prefix="test-review409-")
    bpath = os.path.join(tmpd, "bundle.bin")
    open(bpath, "wb").write(blob2)
    tpath = os.path.join(tmpd, "plain.tar")
    open(tpath, "wb").write(buf.getvalue())
    xpath = os.path.join(tmpd, "plain.txt")
    open(xpath, "wb").write(txt)
    cpath = os.path.join(tmpd, "corrupt.bin")
    open(cpath, "wb").write(bytes(bad))

    d11, f11 = qa._extract_artifact_files("test-t11", bpath)
    ok11 = (d11 is not None and len(f11) == 2
            and all(os.path.exists(p) for p in f11)
            and open(os.path.join(d11, "add_one.py"), "rb").read() == FILES["add_one.py"])
    check("T11 extract bundle via queue_api", ok11, "files=%s" % f11)

    d12, f12 = qa._extract_artifact_files("test-t12", tpath)
    check("T12 extract plain tar via queue_api",
          d12 is not None and len(f12) == 2, "files=%s" % f12)

    d13, r13 = qa._extract_artifact_files("test-t13", xpath)
    check("T13 plain file rejected for code review",
          d13 is None and r13.startswith("artifact-not-bundle-or-tar"),
          "got=%r" % r13)

    d14, r14 = qa._extract_artifact_files("test-t14", cpath)
    check("T14 corrupt bundle rejected",
          d14 is None and r14.startswith("artifact-extract-fail"),
          "got=%r" % r14)

    # T15: path traversal -- tar with ../../evil.py must not escape tmpd
    trav = os.path.join(tmpd, "trav.tar")
    tb = io.BytesIO()
    with tarfile.open(fileobj=tb, mode="w") as tf:
        for name, data in (("good.py", FILES["add_one.py"]),
                           ("../../evil.py", b"evil")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    open(trav, "wb").write(tb.getvalue())
    d15, f15 = qa._extract_artifact_files("test-t15", trav)
    evil_outside = os.path.realpath(os.path.join(d15 or tmpd, "..", "..", "evil.py")) \
        if d15 else os.path.join(tmpd, "evil.py")
    check("T15 traversal member skipped, legit kept",
          d15 is not None and len(f15) == 1
          and f15[0].endswith("good.py") and not os.path.exists(evil_outside),
          "files=%s evil_exists=%s" % (f15, os.path.exists(evil_outside)))

    # T16: legacy raw-concatenation bundle still parses (verifier compat)
    raw_payload = FILES["add_one.py"] + FILES["test_add_one.py"]
    man16 = {"format": "PULLONCE-BUNDLE-V1",
             "files": [{"path": n, "bytes": len(FILES[n]),
                        "sha256": hashlib.sha256(FILES[n]).hexdigest()}
                       for n in sorted(FILES)]}
    blob16 = (b"PULLONCE-BUNDLE-V1\n" + json.dumps(man16, sort_keys=True).encode()
              + b"\n" + raw_payload)
    got16, kind16 = ab.load_artifact(blob16)
    check("T16 legacy raw-concat bundle", kind16 == "bundle" and got16 == FILES,
          "kind=%s" % kind16)

    print("RESULT pass=%d fail=%d" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
