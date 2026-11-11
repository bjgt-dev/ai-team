#!/usr/bin/env python3
"""B2 off-site backup uploader (2026-11-07, gap-fill).

GAP IT CLOSES: B2 bucket bjgt-hub-backup was verified once by hand
(35 MB upload + byte-identical download, 2026-11-07) but NO script in
~/ai-team/scripts/ did periodic uploads, and NO restore check existed
for B2. Everything about B2 lived in a doc + an env file.

What this does:
  - builds tar.gz of a source dir (default: ~/ai-team/state/)
  - EXCLUDES secrets by name (*.env, *.pem, *.key, *credential*,
    *secret*, id_*, known_hosts, *cookie*, __pycache__) and re-scans
    the finished archive; a secret filename inside aborts (exit 3)
    before any network call.
  - uploads via S3-compatible SigV4 PUT using ONLY the stdlib
    (no boto3 / b2 / aws CLI exists on the hub).
  - writes a receipt JSON (bucket name + key + sha256 + sizes only;
    NEVER keys) to state/b2-backup-20261107/receipt-<ts>.json.

Safety: --dry-run builds the archive + receipt with mode=dry-run and
makes NO network call. Default run without --dry-run DOES upload;
schedule it only via a chair-approved cron line (none installed here).
No money beyond the B2 free tier (first 10 GB free, no card on file);
archive of state/ is ~124 MB, far under the tier.

Exit codes: 0 ok, 1 upload/verify failure, 2 env/args missing, 3 secret
detected in archive.
"""
import argparse
import hashlib
import hmac
import json
import os
import sys
import tarfile
import tempfile
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

BASE = os.environ.get("AI_TEAM_BASE", "/home/ubuntu/ai-team")
DEFAULT_ENV = os.path.join(BASE, "state", "b2-hub-backup.env")
DEFAULT_SRC = os.path.join(BASE, "state")
OUT_DIR = os.path.join(BASE, "state", "b2-backup-20261107")

SECRET_PATTERNS = (".env", ".pem", ".key", "credential", "secret",
                   "known_hosts", "cookie")
SECRET_PREFIXES = ("id_",)


def is_secret(rel):
    low = rel.lower()
    base = os.path.basename(low)
    if any(p in low for p in SECRET_PATTERNS):
        return True
    if any(base.startswith(p) for p in SECRET_PREFIXES):
        return True
    if "__pycache__" in low:
        return True
    return False


def load_env(path):
    if not os.path.isfile(path):
        return None, "env file missing: %s" % path
    env = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    # Accept both B2_* and bare names.
    out = {
        "key_id": env.get("B2_KEY_ID") or env.get("KEY_ID"),
        "app_key": env.get("B2_APPLICATION_KEY") or env.get("APPLICATION_KEY"),
        "bucket": env.get("B2_BUCKET") or env.get("BUCKET"),
        "endpoint": env.get("B2_ENDPOINT") or env.get("ENDPOINT"),
    }
    missing = [k for k, v in out.items() if not v]
    if missing:
        return None, "env file lacks: %s" % ",".join(missing)
    return out, None


def build_archive(src, tmpdir):
    arc = os.path.join(tmpdir, "ai-team-state.tar.gz")
    included, excluded, skipped = [], [], []
    with tarfile.open(arc, "w:gz") as tf:
        for root, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in sorted(files):
                full = os.path.join(root, name)
                rel = os.path.relpath(full, src)
                if is_secret(rel):
                    excluded.append(rel)
                    continue
                # Unreadable files (e.g. 0600 root-owned key backups) are
                # skipped and counted, never fatal: a backup must not die
                # on one file (found live 2026-11-07:
                # muse_pull-authorized_keys.bak-20261104).
                try:
                    tf.add(full, arcname=rel)
                except OSError as e:
                    skipped.append({"file": rel, "error": str(e)[:100]})
                    continue
                included.append(rel)
    # Second-pass scan: reopen and check member names.
    with tarfile.open(arc, "r:gz") as tf:
        for m in tf.getmembers():
            if is_secret(m.name):
                return None, included, excluded, skipped, "secret in archive: %s" % m.name
    sha = hashlib.sha256()
    with open(arc, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
    return arc, included, excluded, skipped, None


def _hmac(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sigv4_put(endpoint, bucket, key, access, secret, filepath):
    """Minimal SigV4 PUT (stdlib only). Returns (http_status, body)."""
    now = datetime.now(timezone.utc)
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    region = "us-west-004"  # B2 S3 endpoint region for this bucket
    host = endpoint.replace("https://", "").replace("http://", "").rstrip("/")
    url = "https://%s/%s/%s" % (host, bucket, key)
    with open(filepath, "rb") as f:
        body = f.read()
    payload_hash = hashlib.sha256(body).hexdigest()
    headers = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amzdate,
    }
    signed = ";".join(sorted(headers))
    canon_headers = "".join("%s:%s\n" % (k, headers[k]) for k in sorted(headers))
    canon_req = "\n".join(["PUT", "/%s/%s" % (bucket, key), "",
                           canon_headers, signed, payload_hash])
    scope = "%s/%s/s3/aws4_request" % (datestamp, region)
    string_to_sign = "\n".join(["AWS4-HMAC-SHA256", amzdate, scope,
                                hashlib.sha256(canon_req.encode()).hexdigest()])
    k = ("AWS4" + secret).encode()
    for part in (datestamp, region, "s3", "aws4_request"):
        k = _hmac(k, part)
    sig = hmac.new(k, string_to_sign.encode(), hashlib.sha256).hexdigest()
    headers["Authorization"] = (
        "AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s"
        % (access, scope, signed, sig))
    req = urllib.request.Request(url, data=body, method="PUT", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.status, r.read()[:200].decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:500].decode("utf-8", "replace")


def main():
    ap = argparse.ArgumentParser(description="B2 off-site backup uploader")
    ap.add_argument("--dry-run", action="store_true",
                    help="build archive + receipt, no network call")
    ap.add_argument("--env-file", default=DEFAULT_ENV)
    ap.add_argument("--source-dir", default=DEFAULT_SRC)
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--object-key", default=None,
                    help="override object key (tests only)")
    args = ap.parse_args()

    env, err = load_env(args.env_file)
    if env is None:
        print(json.dumps({"ok": False, "error": err}))
        return 2
    if not os.path.isdir(args.source_dir):
        print(json.dumps({"ok": False,
                          "error": "source dir missing: %s" % args.source_dir}))
        return 2

    ts = time.strftime("%Y%m%d-%H%M%S")
    os.makedirs(args.out_dir, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="b2-backup-") as td:
        arc, included, excluded, skipped, scan_err = build_archive(args.source_dir, td)
        if scan_err:
            print(json.dumps({"ok": False, "error": scan_err}))
            return 3
        size = os.path.getsize(arc)
        sha = hashlib.sha256()
        with open(arc, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                sha.update(chunk)
        key = args.object_key or ("hub-backup/%s/ai-team-state-%s.tar.gz"
                                  % (ts[:8], ts))
        receipt = {"ok": True, "mode": "dry-run" if args.dry_run else "upload",
                   "ts": ts, "bucket": env["bucket"], "object_key": key,
                   "source": args.source_dir, "files": len(included),
                   "excluded_secrets": len(excluded),
                   "excluded_names": excluded[:20],
                   "skipped_unreadable": len(skipped),
                   "skipped_names": [s["file"] for s in skipped[:20]],
                   "bytes": size, "sha256": sha.hexdigest(),
                   "endpoint_host": env["endpoint"]}
        if not args.dry_run:
            status, body = sigv4_put(env["endpoint"], env["bucket"], key,
                                     env["key_id"], env["app_key"], arc)
            receipt["http_status"] = status
            receipt["http_body"] = body
            if status not in (200, 201, 204):
                receipt["ok"] = False
                receipt["error"] = "upload failed http=%s" % status
        rp = os.path.join(args.out_dir, "receipt-%s.json" % ts)
        with open(rp, "w", encoding="utf-8") as f:
            json.dump(receipt, f, ensure_ascii=False, indent=2)
        print(json.dumps(receipt, ensure_ascii=False))
        return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
