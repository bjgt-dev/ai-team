#!/usr/bin/env python3
# Transcription consistency check: injected NCs vs decomposition NC field.
# Detects rewrite/drift that schema validation cannot catch.
# Usage: consistency_check.py <case_dir> [more case dirs]
import difflib, json, os, sys


def iter_tasks(obj):
    if isinstance(obj, list):
        for t in obj:
            yield from iter_tasks(t)
    elif isinstance(obj, dict):
        yield obj
        for k in ("tasks", "subtasks", "steps"):
            if isinstance(obj.get(k), list):
                yield from iter_tasks(obj[k])


def ratio(a, b):
    return round(difflib.SequenceMatcher(None, a or "", b or "").ratio(), 3)


for case_dir in sys.argv[1:]:
    out = {"case_dir": case_dir}
    try:
        injected = json.load(open(os.path.join(case_dir, "extracted_ncs.json")))
        decomp = json.load(open(os.path.join(case_dir, "decomposition.json")))
    except Exception as e:
        out["error"] = str(e)[:160]
        print(json.dumps(out, ensure_ascii=False))
        continue
    entries = {}
    for t in iter_tasks(decomp):
        for item in (t.get("negative_constraints") or []):
            if isinstance(item, dict) and item.get("id"):
                entries[item["id"]] = item
    rows = []
    for nc in injected:
        e = entries.get(nc["id"])
        if e is None:
            rows.append({"id": nc["id"], "found": False})
            continue
        rows.append({
            "id": nc["id"], "found": True,
            "source_exact": e.get("source_text") == nc["source_text"],
            "rule_ratio": ratio(e.get("rule"), nc["rule"]),
            "check_ratio": ratio(e.get("acceptance_check"), nc["acceptance_check"]),
        })
    out["rows"] = rows
    out["all_found"] = all(r.get("found") for r in rows)
    out["all_source_exact"] = all(r.get("source_exact") for r in rows if r.get("found"))
    print(json.dumps(out, ensure_ascii=False))
