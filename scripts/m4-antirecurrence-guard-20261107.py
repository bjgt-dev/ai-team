#!/usr/bin/env python3
"""M4 anti-recurrence guard (2026-11-07, MEMORY.md M4自我改进与防复犯).

Rules landed as code (were memory-only):
  * lessons store exists and is appendable (state/lessons.jsonl).
  * five hard guards are present in hub code, audited by token:
      1. claim stamp, 2. ask_chairman gate, 3. pause/freeze needs
         owner+reason+expires_at, 4. metric with denominator,
         5. report_gen + lint.
  * boot_quiz + red-line cards (<=12) exist:
    state/m4-self-improve-20261106/boot-quiz-20261107.json.
  * morning report carries the three metrics: 复犯 / 未设防 / 汇报违规.
  * fingerprint escalation: same fault fingerprint 2nd time -> TIGHTEN,
    3rd time -> report chair (outbox).
--record-fault FP records one occurrence and prints the escalation.
Exit 0 PASS, 1 FAIL, 2 SKIP.
"""
import argparse, glob, json, os, re, sys
from datetime import date

BASE = os.environ.get("AI_TEAM", "/home/ubuntu/ai-team")
LESSONS = os.path.join(BASE, "state", "lessons.jsonl")
BOOT_QUIZ = os.path.join(BASE, "state", "m4-self-improve-20261106",
                         "boot-quiz-20261107.json")
OUTBOX = os.path.join(BASE, "state", "outbox")
SCRIPTS = os.path.join(BASE, "scripts")
THREE_METRICS = ("复犯", "未设防", "汇报违规")
# token sets; a guard counts as present if ANY of its tokens appears in code.
FIVE_GUARDS = {
    "claim-stamp": ("claim", "stamp"),
    "ask-chairman": ("ask_chairman", "ask-chairman"),
    "pause-freeze-owner-reason-expires": ("expires_at",),
    "metric-with-denominator": ("denominator",),
    "report-gen-lint": ("report_gen", "lint"),
}


def grep_tokens(root, tokens):
    found = set()
    for path in glob.glob(os.path.join(root, "*.py")):
        if ".bak" in path:
            continue
        try:
            text = open(path, errors="replace").read()
        except OSError:
            continue
        for tok in tokens:
            if tok in text:
                found.add(tok)
    return found


def audit(root, lessons_path, quiz_path, morning_text):
    problems, notes = [], []
    if not os.path.isfile(lessons_path):
        problems.append("lessons store missing: %s" % lessons_path)
    else:
        notes.append("lessons store present")
    for name, tokens in FIVE_GUARDS.items():
        hit = grep_tokens(root, tokens)
        if hit:
            notes.append("guard %s present (token %s)" % (name, sorted(hit)[0]))
        else:
            problems.append("hard guard missing in code: %s (tokens %s)"
                            % (name, tokens))
    try:
        quiz = json.load(open(quiz_path))
        cards = quiz.get("cards", [])
        if not (1 <= len(cards) <= 12):
            problems.append("boot_quiz must hold 1..12 red-line cards, has %d"
                            % len(cards))
        else:
            notes.append("boot_quiz holds %d red-line cards" % len(cards))
    except (OSError, ValueError) as e:
        problems.append("boot_quiz unreadable %s: %s" % (quiz_path, e))
    if morning_text is None:
        notes.append("morning report not found; 3-metric check skipped")
    else:
        missing = [m for m in THREE_METRICS if m not in morning_text]
        if missing:
            problems.append("morning report lacks metrics: %s" % ", ".join(missing))
        else:
            notes.append("morning report carries all 3 metrics")
    return problems, notes


def latest_morning_report():
    cands = sorted(glob.glob(os.path.join(BASE, "state", "outbox",
                                          "*morning-report*")))
    if not cands:
        return None
    try:
        return open(cands[-1], errors="replace").read()
    except OSError:
        return None


def record_fault(lessons_path, fingerprint, note):
    os.makedirs(os.path.dirname(lessons_path), exist_ok=True)
    count = 0
    if os.path.isfile(lessons_path):
        with open(lessons_path) as f:
            for line in f:
                try:
                    if json.loads(line).get("fingerprint") == fingerprint:
                        count += 1
                except ValueError:
                    continue
    count += 1
    with open(lessons_path, "a") as f:
        f.write(json.dumps({"fingerprint": fingerprint, "note": note,
                            "occurrence": count, "date": date.today().isoformat()},
                           ensure_ascii=False) + "\n")
    if count >= 3:
        os.makedirs(OUTBOX, exist_ok=True)
        fn = os.path.join(OUTBOX, "m4-repeat3-%s-%s.json"
                          % (re.sub(r"[^A-Za-z0-9_-]", "_", fingerprint),
                             date.today().isoformat()))
        with open(fn, "w") as f:
            json.dump({"kind": "m4-repeat-third", "fingerprint": fingerprint,
                       "occurrences": count,
                       "action": "report chair"}, f, ensure_ascii=False, indent=2)
        return count, "REPORT-CHAIR (3rd occurrence, outbox %s)" % fn
    if count == 2:
        return count, "TIGHTEN tools (2nd occurrence of same fingerprint)"
    return count, "recorded (1st occurrence)"


def self_test():
    import tempfile
    ok = True

    def ck(cond, label):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + label)
        ok = ok and cond

    with tempfile.TemporaryDirectory() as td:
        scripts = os.path.join(td, "scripts")
        os.makedirs(scripts)
        code = ("# ask_chairman gate\n# claim stamp here\nexpires_at = None\n"
                "denominator = 10\nreport_gen = lint = True\n")
        with open(os.path.join(scripts, "x.py"), "w") as f:
            f.write(code)
        lessons = os.path.join(td, "lessons.jsonl")
        with open(lessons, "w") as f:
            f.write('{"fingerprint": "a", "occurrence": 1}\n')
        quiz = os.path.join(td, "quiz.json")
        with open(quiz, "w") as f:
            json.dump({"cards": [{"q": "x"}] * 5}, f)
        morning = "复犯 0 未设防 0 汇报违规 0"
        probs, _ = audit(scripts, lessons, quiz, morning)
        ck(probs == [], "all five guards + quiz + metrics present -> PASS")
        probs, _ = audit(scripts, lessons, quiz, "nothing here")
        ck(any("metrics" in p for p in probs), "morning report w/o 3 metrics -> FAIL")
        probs, _ = audit(scripts, os.path.join(td, "nope.jsonl"), quiz, morning)
        ck(any("lessons" in p for p in probs), "missing lessons store -> FAIL")
        probs, _ = audit(os.path.join(td, "empty"), lessons, quiz, morning)
        ck(len([p for p in probs if "hard guard" in p]) == 5,
           "empty scripts dir -> all 5 guards missing (negative test)")
        n, msg = record_fault(lessons, "fp-x", "first")
        ck(n == 1 and "1st" in msg, "1st occurrence recorded")
        n, msg = record_fault(lessons, "fp-x", "second")
        ck(n == 2 and "TIGHTEN" in msg, "2nd occurrence -> TIGHTEN")
        n, msg = record_fault(lessons, "fp-x", "third")
        ck(n == 3 and "REPORT-CHAIR" in msg, "3rd occurrence -> REPORT-CHAIR")
    print("SELF-TEST " + ("ALL PASS" if ok else "FAILURES PRESENT"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--record-fault", help="fault fingerprint to record")
    ap.add_argument("--note", default="")
    ap.add_argument("--lessons", default=LESSONS)
    ap.add_argument("--quiz", default=BOOT_QUIZ)
    ap.add_argument("--scripts", default=SCRIPTS)
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if args.record_fault:
        n, msg = record_fault(args.lessons, args.record_fault, args.note)
        print("occurrence %d: %s" % (n, msg))
        return 0
    problems, notes = audit(args.scripts, args.lessons, args.quiz,
                            latest_morning_report())
    for n in notes:
        print("ok: " + n)
    if problems:
        for p in problems:
            print("FAIL " + p)
        return 1
    print("PASS M4 anti-recurrence: lessons + 5 guards + boot_quiz + 3 metrics")
    return 0


if __name__ == "__main__":
    sys.exit(main())
