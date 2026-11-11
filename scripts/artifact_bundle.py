#!/usr/bin/env python3
"""artifact_bundle.py -- PULLONCE-BUNDLE-V1 artifact format, canonical module.

Format convention (unified 2026-11-10, review-409 thorough fix; the same
convention is recorded in knowledge/ops-review409-fix-20261110.md):

  Artifact bytes = header line + manifest line + payload
    line 1:  b"PULLONCE-BUNDLE-V1\\n"            (BUNDLE_MAGIC)
    line 2:  JSON manifest, one line, sort_keys
             {"format": "PULLONCE-BUNDLE-V1",
              "files": [{"path": <str>, "bytes": <int>, "sha256": <hex>}],
              "bundle_bytes": <int>,        # len(payload)
              "bundle_sha256": <hex>}       # sha256(payload)
             Extra manifest fields (e.g. "pytest") are allowed and ignored.
    payload: gzip-compressed tar of exactly the manifest files
             (tarfile mode "w:gz", deterministic sorted order).
             A legacy raw-concatenation payload (manifest files in order,
             no compression) is accepted for old fixtures only; the
             producer never emits it.

  Plain artifacts (no bundle header) are also recognised by load_artifact:
    kind "tar"  -- a plain tar (or tar.gz) of files, no header/manifest.
    kind "file" -- any other single file (e.g. a lone .py, pytest text).

  Who produces what:
    * pull_once.py _bundle_workdir (worker-1) produces bundles for code
      tasks; taskcontract_gate REQUIRES code tasks to declare
      format PULLONCE-BUNDLE-V1. The bundle is the contract-mandated
      format, not a deviation.
    * script tasks produce plain stdout text (kind "file").
    * a code task with no bundleable files produces plain pytest text.

  Who parses what (after this fix, all format-aware):
    * c2-verifier / verifier-gates-234-20261107.py unpack_bundle -- the
      reference parser; this module mirrors its semantics exactly.
    * queue_api _ensure_review -- uses load_artifact() from this module.
    * This module is the single canonical parser for the review path;
      do NOT re-implement bundle parsing elsewhere (the 2026-11-10
      review-409 bug was exactly a second, wrong parser: raw
      tarfile.open() on a bundle).

  Security: parse/load return {path: bytes} in memory only; they never
  write to disk, so tar path traversal cannot escape. Callers that write
  files to disk MUST validate each path stays inside the target dir
  (reject absolute paths and ".." components).

  Fail-closed: parse_bundle raises ValueError on ANY malformed bundle
  or sha mismatch; load_artifact raises ValueError only on unreadable
  input, never returns partial files.
"""
import gzip
import hashlib
import io
import json
import os
import tarfile

BUNDLE_MAGIC = b"PULLONCE-BUNDLE-V1\n"
GZIP_MAGIC = b"\x1f\x8b"


def parse_bundle(data):
    """Parse a PULLONCE-BUNDLE-V1 artifact.

    Returns {path: bytes} if data starts with BUNDLE_MAGIC, else None.
    Raises ValueError on a malformed bundle or any sha mismatch
    (tool error; callers must fail closed, never review partial files).
    Semantics mirror verifier-gates-234-20261107.py unpack_bundle.
    """
    if not isinstance(data, (bytes, bytearray)):
        return None
    if not data.startswith(BUNDLE_MAGIC):
        return None
    try:
        nl1 = data.index(b"\n", len(BUNDLE_MAGIC))
    except ValueError:
        raise ValueError("bundle: manifest line missing")
    try:
        manifest = json.loads(data[len(BUNDLE_MAGIC):nl1].decode("utf-8"))
    except Exception as exc:
        raise ValueError("bundle: manifest is not JSON: %s" % exc)
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, list):
        raise ValueError("bundle: manifest has no files list")
    payload = bytes(data[nl1 + 1:])
    expected = {e.get("path"): e for e in files if isinstance(e, dict)}
    if payload[:2] == GZIP_MAGIC:
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
    # Legacy raw-concatenation payload (old fixtures only; producer never emits).
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


def load_artifact(path_or_data):
    """Load an artifact file (or raw bytes).

    Returns (files: {path: bytes}, kind: str) with kind in
    "bundle" | "tar" | "file". Raises OSError on unreadable input and
    ValueError on a malformed bundle (fail-closed). A plain tar is
    recognised by tarfile, not by magic; anything else is one file.
    """
    if isinstance(path_or_data, (bytes, bytearray)):
        data = bytes(path_or_data)
        name = "artifact"
    else:
        with open(path_or_data, "rb") as f:
            data = f.read()
        name = os.path.basename(str(path_or_data))
    files = parse_bundle(data)
    if files is not None:
        return files, "bundle"
    try:
        out = {}
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                f = tf.extractfile(member)
                out[member.name] = f.read() if f is not None else b""
        if out:
            return out, "tar"
    except (tarfile.TarError, OSError, EOFError):
        pass
    return {name: data}, "file"


def build_bundle(files, extra=None):
    """Build PULLONCE-BUNDLE-V1 bytes from {path: bytes} (producer mirror).

    Used by tests and by any future producer; the worker producer
    (pull_once.py _bundle_workdir) builds the same layout. mtime is 0
    so test vectors are deterministic.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name in sorted(files):
            data = files[name]
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            tf.addfile(info, io.BytesIO(data))
    gz_bytes = buf.getvalue()
    manifest = {"format": "PULLONCE-BUNDLE-V1",
                "files": [{"path": n, "bytes": len(files[n]),
                           "sha256": hashlib.sha256(files[n]).hexdigest()}
                          for n in sorted(files)],
                "bundle_bytes": len(gz_bytes),
                "bundle_sha256": hashlib.sha256(gz_bytes).hexdigest()}
    if extra:
        manifest.update(extra)
    header = (b"PULLONCE-BUNDLE-V1\n" +
              json.dumps(manifest, sort_keys=True).encode() + b"\n")
    return header + gz_bytes
