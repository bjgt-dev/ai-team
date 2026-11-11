#!/usr/bin/env python3
# Independent Verifier gate (2026-10-21 Phase C).
# Generator-Evaluator separation: reads ONLY the original requirement and the
# decomposed contract(s); judges whether every negative constraint in the
# requirement is present in the contract negative_constraints with a
# mechanically checkable acceptance_check.
# Usage: verifier.py <requirement.txt> <contract.json> [more.json ...]
#        [--attempt N] [--model MODEL]
# Exit: 0 PASS, 2 REJECT, 1 error. Stdout: one JSON verdict.
import json, re, sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TRIGGERS = ["不", "不要", "不得", "不能", "不许", "禁止", "严禁", "切勿", "勿",
            "只", "仅", "只能", "仅限", "唯", "必须不", "避免", "无需", "不需要"]
DEFAULT_MODEL = "gpt-oss-120b"
NEWAPI_BASE = "http://127.0.0.1:3001/v1"


def split_sentences(text):
    parts = re.split(r"[。！？；;\n]+", text)
    return [p.strip() for p in parts if p.strip()]


def find_candidates(text):
    out = []
    for s in split_sentences(text):
        if any(t in s for t in TRIGGERS):
            out.append(s)
    return out


def collect_contract(data):
    tasks = data if isinstance(data, list) else [data]
    ncs, accs, objs = [], [], []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        if isinstance(t.get("negative_constraints"), list):
            ncs.extend([x for x in t["negative_constraints"] if isinstance(x, dict)])
        ac = t.get("acceptance_criteria")
        if isinstance(ac, list):
            for a in ac:
                accs.append(a if isinstance(a, str) else json.dumps(a, ensure_ascii=False))
        if isinstance(t.get("objective"), str):
            objs.append(t["objective"])
        for key in ("tasks", "subtasks", "steps"):
            sub = t.get(key)
            if isinstance(sub, list):
                n2, a2, o2 = collect_contract(sub)
                ncs += n2; accs += a2; objs += o2
    return ncs, accs, objs


def llm_judge(model, candidates, ncs, accs):
    import urllib.request, urllib.error, time
    import auto_ceo
    key = auto_ceo.read_key()
    prompt = (
        "你是一个独立校验器。只根据给出的材料判断，不要脑补。\n"
        "【候选句】（从原需求中按否定词扫描得到，逐条编号）:\n"
        + "\n".join("CAND-%d: %s" % (i + 1, s) for i, s in enumerate(candidates))
        + "\n【契约的 negative_constraints】:\n"
        + (json.dumps(ncs, ensure_ascii=False) if ncs else "（无此字段或为空）")
        + "\n【契约的 acceptance_criteria】:\n"
        + ("\n".join("- " + a for a in accs) if accs else "（无）")
        + "\n对每个候选句输出一项判断:\n"
        "- is_constraint: 该句是否构成对执行者的限制（陈述性句子如\"不确定\"不算，填 false 并给 reason）\n"
        "- matched_id: 若该约束已写入 negative_constraints，填对应条目的 id；没有对应条目填 null\n"
        "- checkable: 对应条目的 acceptance_check 是否可机械校验（必须含计数、集合成员、字段值、格式锚点至少其一；只有\"诚实/简短/尽量\"等态度词为 false）；matched_id 为 null 时填 false\n"
        "- reason: 一句话理由\n"
        "只输出 JSON 数组，不要输出任何其他文字。格式: "
        "[{\"cand\": 1, \"is_constraint\": true, \"matched_id\": \"NC-01\" 或 null, \"checkable\": true, \"reason\": \"...\"}]"
    )
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 6000,
    }).encode()
    req = urllib.request.Request(
        NEWAPI_BASE + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    data = None
    last_err = None
    for attempt_i in range(4):
        try:
            with urllib.request.urlopen(req, timeout=240) as resp:
                data = json.loads(resp.read())
            break
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code != 429 or attempt_i == 3:
                raise
            wait = 20 * (attempt_i + 1)
            ra = e.headers.get("Retry-After") if e.headers else None
            if ra and ra.isdigit():
                wait = min(int(ra), 90)
            time.sleep(wait)
    if data is None:
        raise last_err
    content = data["choices"][0]["message"]["content"]
    m = re.search(r"\[.*\]", content, re.S)
    if not m:
        raise ValueError("judge output has no JSON array: " + content[:300])
    return json.loads(m.group(0))


def main(argv):
    attempt = 1
    model = DEFAULT_MODEL
    args = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--attempt" and i + 1 < len(argv):
            attempt = int(argv[i + 1]); i += 2; continue
        if a == "--model" and i + 1 < len(argv):
            model = argv[i + 1]; i += 2; continue
        if a.startswith("--"):
            i += 1; continue
        args.append(a); i += 1
    if len(args) < 2:
        print(json.dumps({"error": "usage: verifier.py <requirement.txt> <contract.json> [...]"}))
        return 1
    requirement = open(args[0], encoding="utf-8").read()
    ncs, accs, objs = [], [], []
    for p in args[1:]:
        n2, a2, o2 = collect_contract(json.load(open(p, encoding="utf-8")))
        ncs += n2; accs += a2; objs += o2
    candidates = find_candidates(requirement)
    verdict = {"decision": "PASS", "attempt": attempt, "checked": [],
               "missing": [], "unverifiable": [], "excluded": []}
    if not candidates:
        print(json.dumps(verdict, ensure_ascii=False))
        return 0
    judgments = llm_judge(model, candidates, ncs, accs)
    by_cand = {j.get("cand"): j for j in judgments if isinstance(j, dict)}
    for i, s in enumerate(candidates):
        j = by_cand.get(i + 1)
        if j is None:
            verdict["missing"].append({"source_text": s, "reason": "校验器未对该候选句给出判断，按缺失处理"})
            continue
        if not j.get("is_constraint"):
            verdict["excluded"].append({"source_text": s, "reason": str(j.get("reason", "非约束性陈述"))})
        elif not j.get("matched_id"):
            verdict["missing"].append({"source_text": s, "reason": str(j.get("reason", "契约 negative_constraints 无对应项"))})
        elif not j.get("checkable"):
            verdict["unverifiable"].append({"id": str(j["matched_id"]), "reason": str(j.get("reason", "acceptance_check 不可机械校验"))})
        else:
            verdict["checked"].append(str(j["matched_id"]))
    if verdict["missing"] or verdict["unverifiable"]:
        verdict["decision"] = "REJECT"
    print(json.dumps(verdict, ensure_ascii=False))
    return 0 if verdict["decision"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
