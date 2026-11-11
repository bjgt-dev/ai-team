#!/usr/bin/env python3
"""task-evidence.py — one-click evidence receipt packer (Thorsten Ball Rule 5, 2026-11-07).
Given a task_id and deliverable files, compute sha256 + bytes for each,
bundle the raw output tail, and write a receipt JSON.
Usage: python3 scripts/task-evidence.py --task <task_id> --files <f1,f2,...>
       [--raw <raw-output-file>] [--out <dir, default state/evidence>]"""
import argparse, hashlib, json, os, sys, time

BASE = "/home/ubuntu/ai-team"
DEFAULT_OUT = BASE + "/state/evidence"

def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def main():
    ap = argparse.ArgumentParser(description="Pack an evidence receipt for a task")
    ap.add_argument("--task", required=True, help="task_id")
    ap.add_argument("--files", required=True, help="comma-separated deliverable file paths")
    ap.add_argument("--raw", help="raw test/output file to embed the tail of")
    ap.add_argument("--out", default=DEFAULT_OUT, help="receipt output dir")
    args = ap.parse_args()
    files = []
    missing = []
    for p in [x.strip() for x in args.files.split(",") if x.strip()]:
        if not os.path.isfile(p):
            missing.append(p)
            continue
        files.append({"path": p, "sha256": sha256_of(p), "bytes": os.path.getsize(p)})
    if missing:
        print("task-evidence: MISSING files: %s" % ", ".join(missing))
        return 1
    if not files:
        print("task-evidence: no files given")
        return 1
    receipt = {"task_id": args.task, "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "files": files}
    if args.raw:
        if not os.path.isfile(args.raw):
            print("task-evidence: raw file missing: %s" % args.raw)
            return 1
        with open(args.raw, encoding="utf-8", errors="ignore") as f:
            lines = f.read().splitlines()
        receipt["raw_output_file"] = args.raw
        receipt["raw_output_sha256"] = sha256_of(args.raw)
        receipt["raw_output_tail"] = lines[-50:]
    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, "%s-receipt.json" % args.task)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(receipt, f, ensure_ascii=False, indent=2)
    print("task-evidence: receipt written %s" % out_path)
    for fl in files:
        print("  %s  %dB  sha256=%s" % (fl["path"], fl["bytes"], fl["sha256"]))
    if args.raw:
        print("  raw output: %s (%d lines, tail %d)" % (args.raw, len(lines), len(receipt["raw_output_tail"])))
    return 0

if __name__ == "__main__":
    sys.exit(main())
