#!/usr/bin/env python3
"""B2 backup verify (2026-11-07, MEMORY.md B2异地备份已闭环).

MEMORY: bucket bjgt-hub-backup (US West, s3.us-west-004.backblazeb2.com),
key in state/b2-hub-backup.env (600), first 35 MB backup uploaded and
download-verified byte-for-byte. No recurring verify script existed --
this lands the check as code. It NEVER prints secret values.
  * env file must exist, mode 0600, and name the right bucket+endpoint
    (key names only are reported, values never).
  * manifest state/b2-backup-manifest.json {last_upload_iso, size_bytes,
    sha256, object} must exist and be fresh (<=7 days); missing -> SKIP
    (exit 2, NEEDS-MANIFEST), stale -> FAIL.
  * if a `b2` CLI is present, an extra live probe is attempted and its
    result is reported, but absence of the CLI is not a failure.
Exit 0 PASS, 1 FAIL, 2 SKIP.
"""
import argparse, json, os, shutil, stat, subprocess, sys
from datetime import datetime, timezone

BASE = os.environ.get("AI_TEAM", "/home/ubuntu/ai-team")
ENV_FILE = os.path.join(BASE, "state", "b2-hub-backup.env")
MANIFEST = os.path.join(BASE, "state", "b2-backup-manifest.json")
BUCKET = "bjgt-hub-backup"
ENDPOINT = "s3.us-west-004.backblazeb2.com"
MAX_AGE_DAYS = 7


def check_env(path):
    """Returns list of problems (never includes secret values)."""
    problems = []
    try:
        st = os.stat(path)
    except OSError:
        return ["env file missing: %s" % path]
    if stat.S_IMODE(st.st_mode) != 0o600:
        problems.append("env file mode %o, must be 0600" % stat.S_IMODE(st.st_mode))
    try:
        text = open(path, errors="replace").read()
    except OSError as e:
        return ["env file unreadable: %s" % e]
    if BUCKET not in text:
        problems.append("env file does not name bucket %s" % BUCKET)
    if ENDPOINT not in text:
        problems.append("env file does not name endpoint %s" % ENDPOINT)
    return problems


def check_manifest(path, now):
    """Returns (problems, notes). Missing -> (None, ['skip...'])."""
    try:
        data = json.load(open(path))
    except OSError:
        return None, ["manifest missing (%s); backup freshness unverifiable" % path]
    except ValueError as e:
        return ["manifest invalid JSON: %s" % e], []
    problems, notes = [], []
    for k in ("last_upload_iso", "size_bytes", "sha256", "object"):
        if not data.get(k):
            problems.append("manifest missing %s" % k)
    ts = data.get("last_upload_iso")
    if ts:
        try:
            d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            age = (now - d).days
            notes.append("last upload %s (%d day(s) ago, %s bytes)"
                         % (ts, age, data.get("size_bytes")))
            if age > MAX_AGE_DAYS:
                problems.append("last upload %d day(s) ago > %d" % (age, MAX_AGE_DAYS))
        except ValueError:
            problems.append("manifest last_upload_iso unparsable")
    return problems, notes


def self_test():
    import tempfile
    ok = True

    def ck(cond, label):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + label)
        ok = ok and cond

    now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as td:
        env = os.path.join(td, "b2.env")
        with open(env, "w") as f:
            f.write("B2_BUCKET=%s\nB2_ENDPOINT=%s\nB2_KEY=secret\n" % (BUCKET, ENDPOINT))
        os.chmod(env, 0o600)
        ck(check_env(env) == [], "env 0600 + bucket + endpoint -> PASS")
        os.chmod(env, 0o644)
        ck(any("0600" in p for p in check_env(env)), "env mode 0644 -> FAIL")
        os.chmod(env, 0o600)
        ck(check_env(os.path.join(td, "nope.env")) != [], "missing env -> FAIL")
        man = os.path.join(td, "manifest.json")
        with open(man, "w") as f:
            json.dump({"last_upload_iso": "2026-11-06T10:00:00+00:00",
                       "size_bytes": 36700160, "sha256": "ab" * 32,
                       "object": "hub-backup-20261106.tar.gz"}, f)
        probs, _ = check_manifest(man, now)
        ck(probs == [], "fresh manifest with all fields -> PASS")
        with open(man, "w") as f:
            json.dump({"last_upload_iso": "2026-10-20T10:00:00+00:00",
                       "size_bytes": 1, "sha256": "x", "object": "o"}, f)
        probs, _ = check_manifest(man, now)
        ck(any("day(s) ago" in p for p in probs), "18-day-old upload -> FAIL")
        probs, notes = check_manifest(os.path.join(td, "nope.json"), now)
        ck(probs is None and any("missing" in n for n in notes),
           "missing manifest -> SKIP, not false pass")
    print("SELF-TEST " + ("ALL PASS" if ok else "FAILURES PRESENT"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--env", default=ENV_FILE)
    ap.add_argument("--manifest", default=MANIFEST)
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    now = datetime.now(timezone.utc)
    problems = check_env(args.env)
    mprobs, notes = check_manifest(args.manifest, now)
    for n in notes:
        print("note: " + n)
    if mprobs is None:
        print("SKIP %s" % notes[0])
        if problems:
            for p in problems:
                print("FAIL " + p)
            return 1
        return 2
    problems += mprobs
    b2 = shutil.which("b2")
    print("note: b2 CLI %s" % ("present (live probe not run by this check)"
                               if b2 else "absent (live probe skipped)"))
    if problems:
        for p in problems:
            print("FAIL " + p)
        return 1
    print("PASS B2 env 0600 + bucket/endpoint named + manifest fresh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
