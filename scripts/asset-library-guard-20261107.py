#!/usr/bin/env python3
"""Asset library guard (2026-11-07, MEMORY.md 资产库愿景).

Rule (memory-only until now): assets must be findable and fresh --
structured tags, grade (core/spare/archived), review-by date, and reuse
count. This guard walks state/knowledge-assets/C-knowledge and requires
every asset .md (except INDEX.md) to carry all four fields, in Chinese
or English: 标签/Tags, 分级/Grade, 复核/Review-by, 复用/Reuse.
A past Review-by date is reported as EXPIRED. Files missing fields are
listed; the guard FAILs while any are missing (that is the finding, not
a breakage -- the library predates the rule).
Exit 0 PASS, 1 FAIL, 2 SKIP (library dir missing).
"""
import argparse, glob, os, re, sys
from datetime import date

BASE = os.environ.get("AI_TEAM", "/home/ubuntu/ai-team")
LIB = os.path.join(BASE, "state", "knowledge-assets", "C-knowledge")
FIELDS = {
    "tags": ("标签", "tags"),
    "grade": ("分级", "grade"),
    "review-by": ("复核", "review-by", "review by"),
    "reuse": ("复用", "reuse"),
}
DATE_RE = re.compile(r"20\d\d-\d\d-\d\d")


def check_file(text):
    low = text.lower()
    missing = [name for name, keys in FIELDS.items()
               if not any(k.lower() in low for k in keys)]
    expired = []
    m = re.search(r"(?:复核|review-by|review by)[^\n]*?(" + DATE_RE.pattern + r")",
                  text, re.IGNORECASE)
    if m:
        try:
            if date.fromisoformat(m.group(1)) < date.today():
                expired.append(m.group(1))
        except ValueError:
            pass
    return missing, expired


def self_test():
    ok = True

    def ck(cond, label):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + label)
        ok = ok and cond

    good = "标签：模型\n分级：核心\n复核：2099-01-01\n复用：3 次"
    m, e = check_file(good)
    ck(m == [] and e == [], "all four fields + future review -> PASS")
    m, _ = check_file("标签：x\n分级：核心\n")
    ck(set(m) == {"review-by", "reuse"}, "two fields missing -> listed")
    _, e = check_file(good.replace("2099-01-01", "2026-01-01"))
    ck(e == ["2026-01-01"], "past review-by date -> EXPIRED")
    m, _ = check_file("Tags: model\nGrade: core\nReview-by: 2099-01-01\nReuse: 2")
    ck(m == [], "English field names also accepted")
    print("SELF-TEST " + ("ALL PASS" if ok else "FAILURES PRESENT"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--lib", default=LIB)
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if not os.path.isdir(args.lib):
        print("SKIP library dir missing: %s" % args.lib)
        return 2
    files = sorted(p for p in glob.glob(os.path.join(args.lib, "**", "*.md"),
                                        recursive=True)
                   if os.path.basename(p) != "INDEX.md")
    bad, expired = {}, {}
    for path in files:
        try:
            text = open(path, errors="replace").read()
        except OSError:
            continue
        missing, exp = check_file(text)
        if missing:
            bad[path] = missing
        if exp:
            expired[path] = exp
    print("checked %d asset files" % len(files))
    for path, fields in sorted(bad.items()):
        print("FAIL %s missing: %s" % (path, ", ".join(fields)))
    for path, dates in sorted(expired.items()):
        print("EXPIRED %s review-by %s" % (path, ", ".join(dates)))
    if bad or expired:
        print("FAIL %d file(s) missing fields, %d expired" % (len(bad), len(expired)))
        return 1
    print("PASS all assets tagged, graded, reviewed, reuse-counted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
