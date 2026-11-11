#!/usr/bin/env python3
"""Carpet audit: hub scripts + crontab + watchdogs (read-only).

Lands the audit itself as code (this file) plus the watchdog registry it
generates, closing the 'no unified registry / no dedup / no cron validator'
gap found 2026-11-07:
  * scripts/: 222 live files (176 .py + 46 .sh), 258 .bak files, 0 compile
    failures (py_compile / bash -n), 145 without a dedicated test file,
    176 not referenced by any active cron line (mostly dated one-offs).
  * crontab: 95 lines = 67 active + 28 commented DISABLED/RETIRED (marked,
    intentional). All 56 distinct active targets exist (full-path check);
    8 active lines have NO log redirect (no monitoring); 1 log file never
    created (weekly-audit.log); loop-watchdog.log 0 bytes / stale ~8d while
    its cron line says every 5 min (suspect, needs ops check, NOT fixed
    here — no production change in this audit).
  * watchdogs: 35 watchdog-ish scripts, 19 orphaned (not in active cron),
    no unified registry existed before this file (build_registry.py only
    covers the SKILLS registry).

Usage (on hub):
  python3 scripts/script-cron-audit-20261107.py [--root /home/ubuntu/ai-team]
      [--out state/script-cron-audit-20261107.json]
      [--registry-out state/watchdog-registry-20261107.json]
Prints a JSON summary to stdout; writes the full report + registry only
when --out / --registry-out are given. Exits 0 (clean) or 2 (findings).
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

WATCH_RE = re.compile(r"watch|watchdog|sweep|scan|guard|canary|health|"
                      r"monitor", re.I)
DATE_SUFFIX_RE = re.compile(r"[-_](2026\d{4}|202\d{6})$")


def check_script(path):
    """Compile-check one script. Returns dict."""
    d = {"name": path.name, "path": str(path), "ext": path.suffix,
         "size": path.stat().st_size, "executable": os.access(path, os.X_OK)}
    if path.suffix == ".py":
        r = subprocess.run([sys.executable, "-m", "py_compile", str(path)],
                           capture_output=True, text=True)
    elif path.suffix == ".sh":
        r = subprocess.run(["bash", "-n", str(path)],
                           capture_output=True, text=True)
    else:
        r = None
    d["compile_ok"] = True if r is None else r.returncode == 0
    d["compile_err"] = "" if (r is None or r.returncode == 0) \
        else r.stderr.strip()[:300]
    return d


def scan_scripts(scripts_dir):
    """All live .py/.sh (excluding .bak*). Also counts .bak files."""
    live, bak = [], 0
    for p in sorted(scripts_dir.iterdir()):
        if not p.is_file():
            continue
        if ".bak" in p.name:
            bak += 1
            continue
        if p.suffix in (".py", ".sh"):
            live.append(check_script(p))
    return live, bak


def parse_crontab(text):
    """Split crontab into active lines and disabled lines (commented, with
    the DISABLED/RETIRED tag preserved). Returns (active, disabled)."""
    active, disabled = [], []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            tag = re.match(r"#\s*([A-Z0-9\-]+)?", line)
            disabled.append({"raw": raw,
                             "tag": tag.group(1) if tag else ""})
            continue
        m = re.match(r"^(@\S+|\S+\s+\S+\s+\S+\s+\S+\s+\S+)\s+(.*)$", line)
        if not m:
            active.append({"raw": raw, "parse": "UNPARSED"})
            continue
        cmd = m.group(2)
        targets = sorted(set(re.findall(
            r"/home/ubuntu/[\w\-./]+\.(?:py|sh)", cmd)))
        logm = re.search(r">>\s*(\S+)", cmd)
        active.append({"raw": raw, "schedule": m.group(1), "cmd": cmd,
                       "targets": targets,
                       "missing_targets": [t for t in targets
                                           if not os.path.exists(t)],
                       "log": logm.group(1) if logm else None})
    return active, disabled


def log_status(log_path, now_ts):
    """Freshness of a cron log file: missing / stale>24h / ok (+age h)."""
    if not log_path:
        return {"state": "no-log-redirect"}
    if not os.path.exists(log_path):
        return {"state": "missing"}
    age_h = (now_ts - os.path.getmtime(log_path)) / 3600.0
    return {"state": "stale" if age_h > 24 else "ok",
            "age_hours": round(age_h, 1)}


def watchdog_stem(name):
    """Normalise a watchdog filename for dedup grouping: strip date suffix,
    strip leading NN- prefix, strip extension."""
    s = re.sub(r"\.(py|sh)$", "", name)
    s = re.sub(r"^0?5-", "", s)
    s = DATE_SUFFIX_RE.sub("", s)
    return s


def build_watchdog_registry(scripts, active_cron_text):
    """Group watchdog-ish scripts; flag orphans (not in active cron) and
    near-duplicate stems (e.g. supervisor-stall-scan vs
    05-supervisor-stall-scan)."""
    wd = [s for s in scripts if WATCH_RE.search(s["name"])]
    groups = {}
    for s in wd:
        groups.setdefault(watchdog_stem(s["name"]), []).append(s["name"])
    reg = []
    for s in wd:
        reg.append({"name": s["name"], "stem": watchdog_stem(s["name"]),
                    "in_active_cron": s["name"] in active_cron_text,
                    "compile_ok": s["compile_ok"],
                    "duplicates": [n for n in groups[watchdog_stem(s["name"])]
                                   if n != s["name"]]})
    return reg, {k: v for k, v in groups.items() if len(v) > 1}


def audit(root):
    root = Path(root)
    scripts_dir = root / "scripts"
    scripts, bak_count = scan_scripts(scripts_dir)
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    cron_text = r.stdout if r.returncode == 0 else ""
    active, disabled = parse_crontab(cron_text)
    now_ts = datetime.datetime.now().timestamp()
    cron_text_active = "\n".join(a["raw"] for a in active)
    for a in active:
        a["log_status"] = log_status(a.get("log"), now_ts)
    test_names = {p.name for p in scripts_dir.iterdir()
                  if p.is_file() and "test" in p.name.lower()
                  and ".bak" not in p.name}
    for s in scripts:
        base = re.sub(r"\.(py|sh)$", "", s["name"])
        s["in_active_cron"] = s["name"] in cron_text_active
        s["has_dedicated_test"] = any(base in t for t in test_names) \
            or "test" in s["name"]
    registry, dup_groups = build_watchdog_registry(scripts, cron_text_active)
    report = {
        "audited_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
        "root": str(root),
        "scripts_total": len(scripts),
        "scripts_py": sum(1 for s in scripts if s["ext"] == ".py"),
        "scripts_sh": sum(1 for s in scripts if s["ext"] == ".sh"),
        "bak_files": bak_count,
        "compile_failures": [s for s in scripts if not s["compile_ok"]],
        "scripts_without_test": [s["name"] for s in scripts
                                 if not s["has_dedicated_test"]],
        "scripts_not_in_cron": [s["name"] for s in scripts
                                if not s["in_active_cron"]],
        "cron_total_lines": len(active) + len(disabled),
        "cron_active": len(active),
        "cron_disabled": len(disabled),
        "cron_missing_targets": [a for a in active
                                 if a.get("missing_targets")],
        "cron_no_log": [a["raw"] for a in active
                        if a["log_status"]["state"] == "no-log-redirect"],
        "cron_log_missing": [a for a in active
                             if a["log_status"]["state"] == "missing"],
        "cron_log_stale": [a for a in active
                           if a["log_status"]["state"] == "stale"],
        "cron_active_lines": active,
        "cron_disabled_lines": disabled,
        "watchdog_count": len(registry),
        "watchdog_orphans": [w["name"] for w in registry
                             if not w["in_active_cron"]],
        "watchdog_duplicate_groups": dup_groups,
        "watchdog_registry": registry,
        "scripts": scripts,
    }
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="Hub script+cron+watchdog audit")
    ap.add_argument("--root", default="/home/ubuntu/ai-team")
    ap.add_argument("--out", default=None)
    ap.add_argument("--registry-out", default=None)
    args = ap.parse_args(argv)
    report = audit(args.root)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1,
                                             ensure_ascii=False),
                                  encoding="utf-8")
    if args.registry_out:
        Path(args.registry_out).write_text(
            json.dumps({"generated_at": report["audited_at"],
                        "watchdog_count": report["watchdog_count"],
                        "orphans": report["watchdog_orphans"],
                        "duplicate_groups":
                            report["watchdog_duplicate_groups"],
                        "watchdogs": report["watchdog_registry"]},
                       indent=1, ensure_ascii=False), encoding="utf-8")
    summary = {k: report[k] for k in (
        "audited_at", "scripts_total", "scripts_py", "scripts_sh",
        "bak_files", "cron_total_lines", "cron_active", "cron_disabled",
        "watchdog_count")}
    summary["compile_failures"] = len(report["compile_failures"])
    summary["scripts_without_test"] = len(report["scripts_without_test"])
    summary["scripts_not_in_cron"] = len(report["scripts_not_in_cron"])
    summary["cron_missing_targets"] = len(report["cron_missing_targets"])
    summary["cron_no_log"] = len(report["cron_no_log"])
    summary["cron_log_missing"] = len(report["cron_log_missing"])
    summary["cron_log_stale"] = len(report["cron_log_stale"])
    summary["watchdog_orphans"] = len(report["watchdog_orphans"])
    summary["watchdog_duplicate_groups"] = \
        len(report["watchdog_duplicate_groups"])
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    findings = (summary["compile_failures"]
                + summary["cron_missing_targets"] + summary["cron_no_log"]
                + summary["cron_log_missing"] + summary["cron_log_stale"]
                + summary["watchdog_orphans"])
    return 2 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
