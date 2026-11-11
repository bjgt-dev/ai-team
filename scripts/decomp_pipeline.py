#!/usr/bin/env python3
# Decomposition pipeline with three-layer negative_constraints enforcement
# (2026-10-22 Phase C remediation, 定案: 修机制不换模型).
# Layer 1: deterministic trigger scan + small-model structuring, injected
#          into main's decomposition prompt; main only transcribes.
# Layer 2: output validation (field required, entries complete) + Verifier.
# Layer 3: on validation failure / REJECT, feed the missing list back to main
#          and retry (initial + up to 3 feedback retries); audit every stage.
# Usage: decomp_pipeline.py <case_id> <requirement.txt> <driver_prompt.txt>
#        <out_dir> [--negtest] [--judge-model glm-4-flash-cc]
# Exit: 0 mechanism OK, 2 validation exhausted, 1 error.
import json, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import decomp_gates as G
TRIGGERS = ["务必不", "绝不能", "禁止", "不得", "切勿", "不要", "不能", "不许", "不", "只", "仅"]
GATEWAY = "openclaw-official-openclaw-gateway-1"
NEWAPI_BASE = "http://127.0.0.1:3001/v1"
# 2026-10-22 remediation: reasoner is the decomposition model; weaker models are
# ops-only fallbacks and must be marked (never silently accepted in evaluation).
STRUCT_MODEL = "deepseek-reasoner"
STRUCT_FALLBACK = "glm-4-flash-cc"
MAIN_MODEL_DEFAULT = "my-gateway/deepseek-reasoner"
MAIN_FALLBACKS = ["my-gateway/deepseek-chat", "my-gateway/glm-4-flash-cc"]
AUDIT = "/home/ubuntu/ai-team/logs/decomp-pipeline-audit.jsonl"
BLOCKED_DIR = "/home/ubuntu/ai-team/blocked"
VERIFIER = "/home/ubuntu/ai-team/scripts/verifier.py"


def audit(case, stage, detail):
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "case": case, "stage": stage, "detail": detail}
    try:
        with open(AUDIT, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    print("[audit]", stage, json.dumps(detail, ensure_ascii=False)[:200], flush=True)


def split_sentences(text):
    return [p.strip() for p in re.split(r"[。！？；;\n]+", text) if p.strip()]


def find_candidates(text):
    return [s for s in split_sentences(text) if any(t in s for t in TRIGGERS)]


def llm(model, prompt, max_tokens=4000):
    import urllib.request
    import auto_ceo
    key = auto_ceo.read_key()
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0, "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(NEWAPI_BASE + "/chat/completions", data=body,
                                 headers={"Authorization": "Bearer " + key,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=240) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def structure_candidates(cands):
    if not cands:
        return []
    prompt = (
        "把下面候选句中的否定式约束逐分面拆解：一个分面一条，不得把多个禁止/限制合并成一条。\n"
        "例如同一句里同时有字数上限和行为禁止，必须拆成两条；同一分面只出一条，禁止同义重复，禁止把同一限制换个说法再出一条。逐条输出 JSON 数组，元素字段：\n"
        "- source_text: 该分面在原文中的最小完整片段，逐字照抄\n"
        "- rule: 归一化后的约束表述（一句话，只含这一个分面）\n"
        "- acceptance_check: 必须含可机械校验锚点，至少其一：计数（出现次数=0、≤N、=N）、集合成员（仅限下列集合）、数值上限（字数≤80）、可匹配模式（关键词/短语清单）。不许只写态度词。\n"
        "候选句:\n" + "\n".join("%d. %s" % (i + 1, s) for i, s in enumerate(cands))
        + "\n只输出 JSON 数组。"
    )
    items, used = None, STRUCT_MODEL
    try:
        content = llm(STRUCT_MODEL, prompt)
        m = re.search(r"\[.*\]", content, re.S)
        items = json.loads(m.group(0))
    except Exception:
        items = None
    if not isinstance(items, list) or not items:
        try:
            content = llm(STRUCT_FALLBACK, prompt)
            m = re.search(r"\[.*\]", content, re.S)
            items = json.loads(m.group(0))
            used = STRUCT_FALLBACK
        except Exception:
            items = None
    joined = "\n".join(cands)
    norm = []
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        ac = it.get("acceptance_check", "")
        if not isinstance(ac, str):
            ac = json.dumps(ac, ensure_ascii=False)
        norm.append({"source_text": str(it.get("source_text", "")),
                     "rule": str(it.get("rule", "")),
                     "acceptance_check": ac})
    # anchor repair: a truncated source_text is snapped back to the full candidate
    # sentence it came from (EXH01 root cause: "只能使用这 10 个孩子已接触过的词：")
    import difflib
    for it in norm:
        st = it["source_text"]
        if not st or st.endswith(("：", ":", "、", "，", ",", "；", ";")):
            best = max(cands, key=lambda c: difflib.SequenceMatcher(None, st, c).ratio()) if cands else ""
            it["source_text"] = best
    kept = []
    for it in norm:
        if not it["source_text"] or not it["rule"]:
            continue
        if any(difflib.SequenceMatcher(None, it["rule"], k["rule"]).ratio() > 0.85 for k in kept):
            continue
        if any(difflib.SequenceMatcher(None, it["source_text"], k["source_text"]).ratio() > 0.9
               and difflib.SequenceMatcher(None, it["rule"], k["rule"]).ratio() > 0.7 for k in kept):
            continue
        kept.append(it)
    # deterministic floor: if LLM structuring failed or emptied the list, fall back
    # to rule-based extraction so a bad structuring call can never empty the gate.
    if not kept and cands:
        kept = [{"source_text": c, "rule": c,
                 "acceptance_check": "输出必须逐字保留该约束（计数/集合/数值/模式至少其一可机械校验）"}
                for c in cands]
    out = []
    for i, it in enumerate(kept[:12]):
        out.append({"id": "NC-%02d" % (i + 1),
                    "source_text": it["source_text"],
                    "rule": it["rule"],
                    "acceptance_check": it["acceptance_check"]})
    if out:
        audit("struct", "model_used", {"model": used, "count": len(out)})
    return out


def build_prompt(driver_prompt, ncs):
    return (driver_prompt
            + "\n\n【预抽取的否定式约束（硬性要求）】\n"
            + "以下约束已由预处理步骤抽取。你必须把它们原样写入每个相关任务对象的 negative_constraints 字段"
            + "（id、source_text、rule、acceptance_check 四字段齐全），并把每条 acceptance_check 落进该任务的验收字段。"
            + "不得改写 rule，不得遗漏任何一条：\n"
            + json.dumps(ncs, ensure_ascii=False))


def call_main(prompt, session_id, model=MAIN_MODEL_DEFAULT, allow_fallback=True):
    stub = os.environ.get("DECOMP_MAIN_STUB_FILE")
    if stub:
        with open(stub, encoding="utf-8") as f:
            return f.read(), "stub", False
    models = [model] + ([m for m in MAIN_FALLBACKS if m != model] if allow_fallback else [])
    for i, m in enumerate(models):
        try:
            r = subprocess.run(["docker", "exec", GATEWAY, "openclaw", "agent", "--agent", "main",
                                "--model", m, "--session-id", session_id, "--message", prompt],
                               capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            continue
        out = r.stdout or ""
        if out.strip():
            if i > 0:
                audit(session_id, "model_fallback", {"from": model, "to": m})
            return out, m, i > 0
    return "", model, False


def extract_json(text):
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        return None
    s = min(starts)
    e = max(text.rfind("}"), text.rfind("]"))
    if e <= s:
        return None
    try:
        return json.loads(text[s:e + 1])
    except Exception:
        return None


def iter_tasks(obj):
    if isinstance(obj, list):
        for t in obj:
            yield from iter_tasks(t)
    elif isinstance(obj, dict):
        yield obj
        for k in ("tasks", "subtasks", "steps"):
            if isinstance(obj.get(k), list):
                yield from iter_tasks(obj[k])


def validate_output(obj, ncs, requirement=""):
    """Deterministic gates (schema/coverage/safety/units). Empty list means valid."""
    return G.check_output(obj, ncs, requirement)


def run_case(case, req_text, driver_prompt, out_dir, judge_model,
             model=MAIN_MODEL_DEFAULT, allow_fallback=True):
    os.makedirs(out_dir, exist_ok=True)
    cands = find_candidates(req_text)
    audit(case, "extract_candidates", {"count": len(cands), "candidates": cands})
    ncs = structure_candidates(cands)
    audit(case, "extract_structured", {"count": len(ncs), "items": ncs})
    with open(os.path.join(out_dir, "extracted_ncs.json"), "w", encoding="utf-8") as f:
        json.dump(ncs, f, ensure_ascii=False, indent=2)
    prompt = build_prompt(driver_prompt, ncs)
    obj, problems = None, ["not run"]
    first_json_valid = False
    models_used = []
    for attempt in (1, 2, 3, 4):
        sid = "decomp-pipe-%s-a%d-%d" % (case.lower(), attempt, int(time.time()))
        raw, used_model, fb = call_main(prompt, sid, model, allow_fallback)
        models_used.append({"attempt": attempt, "model": used_model, "fallback": fb})
        with open(os.path.join(out_dir, "attempt%d.raw" % attempt), "w", encoding="utf-8") as f:
            f.write(raw)
        obj = extract_json(raw)
        if attempt == 1:
            first_json_valid = obj is not None
        problems = validate_output(obj, ncs, req_text)
        audit(case, "decompose_attempt", {"attempt": attempt, "valid": not problems,
              "first_json_valid": first_json_valid, "model": used_model,
              "problems": problems[:8]})
        if not problems:
            break
        if attempt < 4:
            prompt = (prompt + "\n\n【校验反馈】你上一次输出未通过校验，问题如下：\n- "
                      + "\n- ".join(problems[:8])
                      + "\n请重新输出完整 JSON，negative_constraints 字段与条目必须齐全，条目原文如下：\n"
                      + json.dumps(ncs, ensure_ascii=False))
            audit(case, "feedback_retry", {"attempt": attempt, "problems": problems[:6]})
    result = {"case": case, "valid": not problems, "problems": problems,
              "attempts_used": attempt, "first_json_valid": first_json_valid,
              "models_used": models_used, "ncs": ncs}
    if problems:
        os.makedirs(BLOCKED_DIR, exist_ok=True)
        bpath = os.path.join(BLOCKED_DIR, "%s-%s.json" % (case.lower(), time.strftime("%Y%m%d%H%M%S")))
        pkg = {"case": case, "status": "blocked",
               "reason": "negative_constraints validation exhausted after feedback retries",
               "attempts_used": attempt, "problems": problems[:8], "ncs_expected": ncs,
               "dispatched": False,
               "disposition": "pending human/CEO approval queue; NOT dispatched"}
        with open(bpath, "w", encoding="utf-8") as f:
            json.dump(pkg, f, ensure_ascii=False, indent=2)
        audit(case, "exhausted_blocked", {"attempts_used": attempt, "problems": problems[:6],
              "blocked_file": bpath, "dispatched": False,
              "disposition": "blocked, not dispatched, awaiting human/CEO decision"})
        audit(case, "alert", {"level": "blocked", "blocked_file": bpath,
              "message": "decomposition blocked: retries exhausted, not dispatched, pending approval"})
        result["blocked_file"] = bpath
    elif obj is not None:
        with open(os.path.join(out_dir, "decomposition.json"), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    return result, obj


def run_verifier(case, req_path, out_path, judge_model):
    r = subprocess.run(["python3", VERIFIER, "--model", judge_model, req_path, out_path],
                       capture_output=True, text=True, timeout=600)
    verdict = None
    try:
        verdict = json.loads((r.stdout or "").strip().splitlines()[-1])
    except Exception:
        verdict = {"parse_error": (r.stdout or "")[:200], "stderr": (r.stderr or "")[:200]}
    audit(case, "verifier", {"exit": r.returncode, "verdict": verdict})
    return r.returncode, verdict


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    negtest = "--negtest" in sys.argv
    judge_model = STRUCT_MODEL
    for i, a in enumerate(sys.argv):
        if a == "--judge-model" and i + 1 < len(sys.argv):
            judge_model = sys.argv[i + 1]
    model = MAIN_MODEL_DEFAULT
    allow_fallback = "--no-fallback" not in sys.argv
    for i, a in enumerate(sys.argv):
        if a == "--model" and i + 1 < len(sys.argv):
            model = sys.argv[i + 1]
    case, req_path, prompt_path, out_dir = args[0], args[1], args[2], args[3]
    req_text = open(req_path, encoding="utf-8").read()
    driver_prompt = open(prompt_path, encoding="utf-8").read()
    result, obj = run_case(case, req_text, driver_prompt, out_dir, judge_model,
                           model=model, allow_fallback=allow_fallback)
    summary = {"case": case, "happy_path": result}
    if result["valid"]:
        code, verdict = run_verifier(case, req_path, os.path.join(out_dir, "decomposition.json"), judge_model)
        summary["verifier"] = {"exit": code, "decision": (verdict or {}).get("decision")}
    if negtest and obj is not None:
        stripped = json.loads(json.dumps(obj))
        for t in iter_tasks(stripped):
            t.pop("negative_constraints", None)
        problems = validate_output(stripped, result["ncs"], req_text)
        audit(case, "negtest_stripped", {"problems": problems[:6], "rejected": bool(problems)})
        summary["negtest"] = {"schema_rejected": bool(problems), "problems": problems[:6]}
        if problems:
            fb_prompt = (build_prompt(driver_prompt, result["ncs"])
                         + "\n\n【校验反馈】你上一次输出被删除了 negative_constraints 字段，校验拒绝。问题：\n- "
                         + "\n- ".join(problems[:8])
                         + "\n请重新输出完整 JSON，字段必须齐全。")
            raw, _m, _f = call_main(fb_prompt, "decomp-pipe-%s-neg1-%d" % (case.lower(), int(time.time())), model, allow_fallback)
            obj2 = extract_json(raw)
            problems2 = validate_output(obj2, result["ncs"], req_text)
            audit(case, "negtest_retry", {"valid": not problems2, "problems": problems2[:6]})
            summary["negtest"]["retry_valid"] = not problems2
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False)[:800], flush=True)
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    sys.exit(main())
