#!/usr/bin/env python3
"""Positive/negative test runner for the 2026-11-07 gateway/backup/
monitor gap-fill scripts. Run ON the hub:
  python3 scripts/gateway-backup-monitor-tests-20261107.py
Uses only temp fixtures except the live Beszel probe (read-only HTTP
+ docker ps) and the live Bailian/SiliconFlow/CF wrapper --help path
(py_compile only for wrappers; wrapper live runs are recorded
separately because the Bailian guard can write guard.log/state).
Prints one PASS/FAIL line per case; exit 0 iff all pass.
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time

SCRIPTS = "/home/ubuntu/ai-team/scripts"
NEW = ["b2-backup-20261107.py", "maritaca-minimax-guard-20261107.py",
       "beszel-health-20261107.py", "muse-sync-check-20261107.py",
       "gateway-backup-monitor-audit-20261107.py",
       "bailian-quota-guard-20261107.py",
       "siliconflow-quota-guard-20261107.py",
       "cf-neuron-guard-20261107.py",
       "gateway-backup-monitor-tests-20261107.py"]
RESULTS = []


def case(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))


def run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, timeout=120, **kw)


def py_compile_all():
    ok = True
    for f in NEW:
        r = run([sys.executable, "-m", "py_compile", os.path.join(SCRIPTS, f)])
        if r.returncode != 0:
            ok = False
            case("py_compile " + f, False, r.stderr[:200])
    if ok:
        case("py_compile all 9 files", True)


def test_b2(td):
    src = os.path.join(td, "state-src")
    os.makedirs(src)
    open(os.path.join(src, "notes.txt"), "w").write("hello\n")
    open(os.path.join(src, "config.env"), "w").write("FAKE=1\n")
    open(os.path.join(src, "id_ed25519"), "w").write("FAKEKEY\n")
    un = os.path.join(src, "unreadable.txt")
    open(un, "w").write("cannot read me\n")
    os.chmod(un, 0)
    envf = os.path.join(td, "b2.env")
    open(envf, "w").write("B2_KEY_ID=fakeid\nB2_APPLICATION_KEY=fakekey\n"
                           "B2_BUCKET=fakebucket\nB2_ENDPOINT=https://example.invalid\n")
    out = os.path.join(td, "b2out")
    r = run([sys.executable, os.path.join(SCRIPTS, "b2-backup-20261107.py"),
             "--dry-run", "--env-file", envf, "--source-dir", src,
             "--out-dir", out])
    try:
        rec = json.loads(r.stdout)
    except Exception:
        rec = {}
    case("b2 dry-run ok, secrets excluded, unreadable skipped",
         r.returncode == 0 and rec.get("ok") is True
         and rec.get("files") == 1 and rec.get("excluded_secrets") == 2
         and rec.get("skipped_unreadable") == 1
         and rec.get("mode") == "dry-run",
         "rc=%s files=%s excl=%s skip=%s" % (r.returncode, rec.get("files"),
                                     rec.get("excluded_secrets"),
                                     rec.get("skipped_unreadable")))
    r2 = run([sys.executable, os.path.join(SCRIPTS, "b2-backup-20261107.py"),
              "--dry-run", "--env-file", os.path.join(td, "no-such.env"),
              "--source-dir", src, "--out-dir", out])
    case("b2 missing env -> exit 2", r2.returncode == 2, "rc=%s" % r2.returncode)


def test_maritaca(td):
    sdir = os.path.join(td, "mm")
    r = run([sys.executable, os.path.join(SCRIPTS, "maritaca-minimax-guard-20261107.py"),
             "--channels-json", '{"26": 0, "28": 1, "29": 1}',
             "--state-dir", sdir, "--no-outbox"])
    case("maritaca stub 28=1,26=0 -> exit 0", r.returncode == 0, "rc=%s" % r.returncode)
    r = run([sys.executable, os.path.join(SCRIPTS, "maritaca-minimax-guard-20261107.py"),
             "--channels-json", '{"26": 0, "28": 0}',
             "--state-dir", sdir, "--no-outbox"])
    case("maritaca stub 28=0 -> exit 1", r.returncode == 1, "rc=%s" % r.returncode)
    r = run([sys.executable, os.path.join(SCRIPTS, "maritaca-minimax-guard-20261107.py"),
             "--channels-json", '{"26": 1, "28": 1}',
             "--state-dir", sdir, "--no-outbox"])
    case("minimax stub 26=1 (re-enabled) -> exit 1", r.returncode == 1, "rc=%s" % r.returncode)


def test_beszel(td):
    sdir = os.path.join(td, "beszel")
    r = run([sys.executable, os.path.join(SCRIPTS, "beszel-health-20261107.py"),
             "--state-dir", sdir, "--no-outbox"])
    case("beszel live hub probe -> exit 0", r.returncode == 0, "rc=%s %s" % (r.returncode, r.stdout[:160]))
    r = run([sys.executable, os.path.join(SCRIPTS, "beszel-health-20261107.py"),
             "--hub-url", "http://127.0.0.1:9/", "--agent-url", "http://127.0.0.1:9/",
             "--no-docker", "--state-dir", sdir, "--no-outbox"])
    case("beszel dead ports -> exit 1", r.returncode == 1, "rc=%s" % r.returncode)


def test_muse_sync(td):
    repo = os.path.join(td, "repo", "backups")
    today = time.strftime("%Y-%m-%d")
    day = os.path.join(repo, today)
    os.makedirs(day)
    data = b"fake-backup-payload"
    open(os.path.join(day, "assets.tar.gz.enc"), "wb").write(data)
    sha = hashlib.sha256(data).hexdigest()
    open(os.path.join(day, "SHA256SUMS"), "w").write("%s  assets.tar.gz.enc\n" % sha)
    drill = os.path.join(td, "drill.json")
    open(drill, "w").write("{}")
    sdir = os.path.join(td, "ms")
    r = run([sys.executable, os.path.join(SCRIPTS, "muse-sync-check-20261107.py"),
             "--repo", os.path.join(td, "repo"), "--state-dir", sdir,
             "--drill-contract", drill])
    case("muse-sync valid sha -> exit 0", r.returncode == 0, "rc=%s" % r.returncode)
    open(os.path.join(day, "assets.tar.gz.enc"), "wb").write(b"tampered")
    r = run([sys.executable, os.path.join(SCRIPTS, "muse-sync-check-20261107.py"),
             "--repo", os.path.join(td, "repo"), "--state-dir", sdir,
             "--drill-contract", drill])
    case("muse-sync tampered sha -> exit 1", r.returncode == 1, "rc=%s" % r.returncode)


def load_checks():
    spec = importlib.util.spec_from_file_location(
        "audit20261107", os.path.join(SCRIPTS, "gateway-backup-monitor-audit-20261107.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.CHECKS


def test_audit(td):
    checks = load_checks()
    base = os.path.join(td, "base")
    scripts = os.path.join(base, "scripts")
    state = os.path.join(base, "state")
    os.makedirs(scripts)
    os.makedirs(state)
    cron_lines = []
    for name, group, script, state_cand, needle, fresh, max_age, *rest in checks:
        if script:
            open(os.path.join(scripts, script), "w").write("# fixture\n")
        if state_cand:
            p = os.path.join(state, state_cand)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w").write("{}\n")
        if fresh and "*" not in fresh:
            p = os.path.join(state, fresh)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w").write("{}\n")
        elif fresh:
            import glob as _g  # noqa
            d = os.path.join(state, os.path.dirname(fresh))
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, "receipt-20261107-000000.json"), "w").write("{}\n")
        if needle:
            cron_lines.append("*/30 * * * * /usr/bin/python3 %s >> /tmp/x.log 2>&1" % needle)
    cronf = os.path.join(td, "crontab.txt")
    open(cronf, "w").write("\n".join(cron_lines) + "\n")
    out = os.path.join(td, "audit.json")
    r = run([sys.executable, os.path.join(SCRIPTS, "gateway-backup-monitor-audit-20261107.py"),
             "--base", base, "--crontab-file", cronf, "--out", out, "--no-outbox"])
    try:
        rec = json.loads(open(out).read())
    except Exception:
        rec = {}
    case("audit fixture all-present -> exit 0",
         r.returncode == 0 and rec.get("counts", {}).get("fail", 99) == 0,
         "rc=%s counts=%s" % (r.returncode, rec.get("counts")))
    empty = os.path.join(td, "empty")
    os.makedirs(os.path.join(empty, "scripts"))
    os.makedirs(os.path.join(empty, "state"))
    cron2 = os.path.join(td, "crontab-empty.txt")
    open(cron2, "w").write("# nothing scheduled\n")
    out2 = os.path.join(td, "audit2.json")
    r = run([sys.executable, os.path.join(SCRIPTS, "gateway-backup-monitor-audit-20261107.py"),
             "--base", empty, "--crontab-file", cron2, "--out", out2, "--no-outbox"])
    try:
        rec2 = json.loads(open(out2).read())
    except Exception:
        rec2 = {}
    case("audit fixture empty -> exit 1 with fails",
         r.returncode == 1 and rec2.get("counts", {}).get("fail", 0) > 0,
         "rc=%s fails=%s" % (r.returncode, rec2.get("counts")))


def main():
    py_compile_all()
    with tempfile.TemporaryDirectory(prefix="gw-audit-test-") as td:
        test_b2(td)
        test_maritaca(td)
        test_beszel(td)
        test_muse_sync(td)
        test_audit(td)
    bad = [r for r in RESULTS if not r[1]]
    print("TOTAL %d passed, %d failed" % (len(RESULTS) - len(bad), len(bad)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
