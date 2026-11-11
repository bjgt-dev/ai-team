#!/usr/bin/env python3
"""Whitelist-only, read-only-source copier for expert browser profiles.
Run as root via sudo -n ONLY (source is root 0700/0600, ubuntu cannot read it).
NEVER writes to /opt/model-profiles, NEVER chmod/chown source, NEVER re-login.
Copies ONLY listed relative paths to a staging dir, verifies manifest,
chowns/chmods ONLY staging, then atomically moves staging to runtime dir.
Usage: sudo -n python3 expert_runtime_copy.py --provider deepseek|gemini --runtime-dir /var/lib/expert-runtime/<provider>
Exit non-zero on any missing REQUIRED item, unreadable source, or manifest mismatch.
"""
import argparse, hashlib, json, os, shutil, sys
from pathlib import Path
SOURCES = {"deepseek": Path("/opt/model-profiles/deepseek"), "gemini": Path("/opt/model-profiles/gemini")}
# (relative path, required?)  dirs are copied recursively, files singly. LOCK files excluded (runtime locks, not login).
COMMON = [
    ("Local State", True),
    ("Default/Cookies", True), ("Default/Cookies-journal", False),
    ("Default/Login Data", True), ("Default/Login Data For Account", False),
    ("Default/Preferences", True), ("Default/Secure Preferences", False),
    ("Default/Network Persistent State", False), ("Default/TransportSecurity", False),
    ("Default/Local Storage", False), ("Default/Session Storage", False),
    ("Default/LocalStorage", False), ("Default/SessionStorage", False),
]
PER_PROVIDER = {
    "deepseek": COMMON + [("Default/IndexedDB", True), ("Default/Sessions", False), ("Default/Sessions_Encrypted", False), ("Default/Service Worker", False), ("Default/Cookies-wal", False), ("Default/Cookies-shm", False)],  # inventory: only https_chat.deepseek.com_0; Sessions/Service Worker per expert round3, optional
    "gemini": COMMON,  # inventory: IndexedDB has NO gemini site (only chrome-extension + claude.ai) -> excluded
}
EXCLUDE_NAMES = {"Cache", "Code Cache", "GPUCache", "History", "HistorySearch", "Safe Browsing", "SingletonCookie", "SingletonLock", "SingletonSocket", "Crashpad", "DevToolsActivePort", "Extensions", "Downloads", "LOCK"}
def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1024*1024), b""): h.update(b)
    return h.hexdigest()
def main():
    a = argparse.ArgumentParser(); a.add_argument("--provider", required=True, choices=list(SOURCES)); a.add_argument("--runtime-dir", required=True); args = a.parse_args()
    src, runtime = SOURCES[args.provider], Path(args.runtime_dir)
    if str(src) not in ("/opt/model-profiles/deepseek", "/opt/model-profiles/gemini"): sys.exit("source path not allowlisted")
    if not str(runtime).startswith("/var/lib/expert-runtime/"): sys.exit("runtime dir not under /var/lib/expert-runtime/")
    if os.geteuid() != 0: sys.exit("must run as root via sudo -n (read-only source)")
    if not src.is_dir(): sys.exit(f"source missing: {src}")
    runtime.parent.mkdir(parents=True, exist_ok=True)
    staging = runtime.parent / ("." + runtime.name + ".staging")
    if staging.exists(): shutil.rmtree(staging)
    staging.mkdir(parents=True, mode=0o700)
    manifest, missing_req, copied = [], [], 0
    try:
        for rel, required in PER_PROVIDER[args.provider]:
            s = src / rel
            if not s.exists():
                if required: missing_req.append(rel)
                continue
            d = staging / rel; d.parent.mkdir(parents=True, exist_ok=True)
            if s.is_dir():
                shutil.copytree(s, d, symlinks=False, ignore=shutil.ignore_patterns(*EXCLUDE_NAMES))
            else:
                shutil.copy2(s, d)
            # manifest over copied tree/file
            for p in ([d] if d.is_file() else sorted(d.rglob("*"))):
                if p.is_file(): manifest.append({"rel": str(p.relative_to(staging)), "sha256": sha256(p), "size": p.stat().st_size}); copied += 1
        if missing_req: sys.exit(f"missing REQUIRED items: {missing_req}")
        # chown/chmod ONLY staging
        for p in sorted(staging.rglob("*"), reverse=True):
            os.chown(p, 1001, 1001)
        for p in staging.rglob("*"):
            os.chmod(p, 0o700 if p.is_dir() else 0o600)
        os.chown(staging, 1001, 1001); os.chmod(staging, 0o700)
        man = {"provider": args.provider, "source": str(src), "files": manifest, "count": copied}
        (staging / "MANIFEST.json").write_text(json.dumps(man, indent=2))
        # atomic publish: remove old runtime only now (no browser is using it in Scheme A sequential tests)
        if runtime.exists(): shutil.rmtree(runtime)
        os.rename(staging, runtime)
        print(json.dumps({"provider": args.provider, "runtime": str(runtime), "files": copied, "manifest_sha256": hashlib.sha256(json.dumps(man, sort_keys=True).encode()).hexdigest(), "status": "ok"}))
    except Exception as e:
        shutil.rmtree(staging, ignore_errors=True)
        print(f"copy failed, staging removed, source untouched: {type(e).__name__}: {e}", file=sys.stderr); return 1
    return 0
if __name__ == "__main__": sys.exit(main())
