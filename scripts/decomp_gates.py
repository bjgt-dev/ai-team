#!/usr/bin/env python3
# Deterministic gates for contract decomposition (2026-10-22 remediation).
# Layers: anchor/span, schema, constraint coverage, safety, units, approval.
# Design rule: gates never call an LLM; they are the hard floor under the model.
import difflib, json, re

UNIT_TOKENS = ["字", "词", "words", "word", "字节", "byte", "bytes", "字符",
               "分钟", "秒", "个", "次", "条", "行", "页", "元", "¥"]
UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(字|词|words?|字节|字符|分钟|秒|个|次|条|行|页|元)")
EN_RE = re.compile(r"[A-Za-z]{2,}")

SAFETY_PAT = re.compile(
    r"编造|虚构|杜撰|不实|隐私|敏感|parent_confirmed|红线|不可逆|不可撤回|发布|对外"
    r"|删除|清空|覆盖|花钱|付款|转账|充值|登录|密码|验证码")
IRREVERSIBLE_PAT = re.compile(r"不可逆|不可撤回|对外|发布|删除|清空|覆盖|上线")

ACCEPT_TYPES = {
    "file_exists", "file_min_size", "content_equals", "content_max_chars",
    "content_max_bytes", "set_members_only", "regex_forbidden", "json_schema",
    "content_contains", "line_count", "row_count",
}
MACHINE_HINTS = ("存在", "非空", "字节", "字符", "字数", "行数", "等于", "包含",
                 "不少于", "不超过", "至多", "至少", "仅", "表格", "列", "行",
                 "table", "exists", "non-empty", "bytes", "chars")

TASK_KEYS = ("tasks", "subtasks", "steps")


def extract_json(text):
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    starts = [m.start() for m in re.finditer(r"[\{\[]", text)]
    for s in starts:
        for e in range(len(text), s, -1):
            try:
                return json.loads(text[s:e])
            except Exception:
                continue
    return None


def iter_tasks(obj):
    out = []
    if isinstance(obj, list):
        for t in obj:
            out.extend(iter_tasks(t))
    elif isinstance(obj, dict):
        out.append(obj)
        for k in TASK_KEYS:
            if isinstance(obj.get(k), list):
                out.extend(iter_tasks(obj[k]))
    return out


def norm(s):
    s = s or ""
    s = s.replace("：", ":").replace("，", ",").replace("、", ",")
    s = s.replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", "", s)


def ratio(a, b):
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def number_units(text):
    return [(m.group(1), m.group(2)) for m in UNIT_RE.finditer(text or "")]


def anchors(text):
    """Anchors that must survive transcription: number+unit, list members, English words."""
    text = text or ""
    out = []
    for n, u in number_units(text):
        out.append(n + u)
    # whitelist / enumerated list after a colon: a、b、c ...
    for chunk in re.findall(r"[:：]\s*([^。；;\n]{4,})", text):
        parts = [p.strip() for p in re.split(r"[、,，/]", chunk) if p.strip()]
        if len(parts) >= 3:
            out.extend(parts)
    out.extend(EN_RE.findall(text))
    seen, uniq = set(), []
    for a in out:
        na = norm(a).lower()
        if na and na not in seen:
            seen.add(na)
            uniq.append(a)
    return uniq


def source_truncated(source_text, requirement):
    st = (source_text or "").strip()
    if len(st) < 4:
        return True
    if st.endswith((":", "：", ",", "，", "、", ";", "；", "(")):
        return True
    return norm(st) not in norm(requirement)


def task_text(t):
    parts = [str(t.get("objective", "")), str(t.get("output_file", "")),
             json.dumps(t.get("acceptance_criteria", ""), ensure_ascii=False),
             json.dumps(t.get("negative_constraints", ""), ensure_ascii=False),
             str(t.get("task_type", ""))]
    return "\n".join(parts)


def check_schema(obj):
    problems = []
    if obj is None:
        return ["SCHEMA: output is not parseable JSON"]
    tasks = iter_tasks(obj)
    if not tasks:
        return ["SCHEMA: no task objects in output"]
    for i, t in enumerate(tasks):
        tag = "task[%d]" % i
        for f in ("task_type", "objective", "output_file", "acceptance_criteria"):
            if not t.get(f):
                problems.append("SCHEMA: %s missing/empty %s" % (tag, f))
        if "negative_constraints" not in t:
            problems.append("SCHEMA: %s missing negative_constraints field" % tag)
        elif not isinstance(t["negative_constraints"], list):
            problems.append("SCHEMA: %s negative_constraints is not an array" % tag)
        if "depends_on" in t and not isinstance(t["depends_on"], list):
            problems.append("SCHEMA: %s depends_on must be an array" % tag)
        ac = t.get("acceptance_criteria")
        if isinstance(ac, list):
            for j, crit in enumerate(ac):
                if isinstance(crit, dict):
                    if crit.get("type") not in ACCEPT_TYPES:
                        problems.append("SCHEMA: %s ac[%d] type not machine-checkable (%s)"
                                        % (tag, j, crit.get("type")))
                    if not crit.get("target"):
                        problems.append("SCHEMA: %s ac[%d] missing target" % (tag, j))
                elif isinstance(crit, str):
                    if not any(h in crit.lower() for h in MACHINE_HINTS):
                        problems.append("SCHEMA: %s ac[%d] subjective (no machine-checkable anchor): %s"
                                        % (tag, j, crit[:40]))
                else:
                    problems.append("SCHEMA: %s ac[%d] must be object or string" % (tag, j))
        elif isinstance(ac, str):
            if not any(h in ac.lower() for h in MACHINE_HINTS):
                problems.append("SCHEMA: %s acceptance_criteria subjective: %s" % (tag, ac[:40]))
        else:
            problems.append("SCHEMA: %s acceptance_criteria missing" % tag)
    return problems


def check_nc_entries(ncs, requirement):
    problems = []
    seen = []
    for it in ncs or []:
        if not isinstance(it, dict):
            problems.append("SCHEMA: negative_constraints entry not an object")
            continue
        for f in ("id", "source_text", "rule", "acceptance_check"):
            if not (isinstance(it.get(f), str) and it[f]):
                problems.append("SCHEMA: NC %s missing/empty %s" % (it.get("id", "?"), f))
        st = it.get("source_text", "")
        if st and source_truncated(st, requirement):
            problems.append("ANCHOR: NC %s source_text truncated/not in requirement: %s"
                            % (it.get("id", "?"), st[:40]))
        for prev in seen:
            if ratio(it.get("rule", ""), prev.get("rule", "")) > 0.92:
                problems.append("ANCHOR: NC %s duplicates %s (rule rewrite/merge)"
                                % (it.get("id", "?"), prev.get("id", "?")))
                break
        seen.append(it)
    return problems


def check_coverage(tasks, ncs):
    problems = []
    all_text = "\n".join(task_text(t) for t in tasks)
    for it in ncs or []:
        if not isinstance(it, dict):
            continue
        goal = norm(it.get("rule", "")) + norm(it.get("acceptance_check", ""))
        miss = []
        for a in anchors(it.get("source_text", "")):
            if norm(a).lower() not in goal.lower():
                miss.append(a)
        # list members: allow acceptance_check to carry the set
        if miss:
            problems.append("COVERAGE: NC %s lost anchors %s" % (it.get("id", "?"), miss[:5]))
        if norm(it.get("source_text", "")) and norm(it.get("source_text", "")) not in norm(all_text):
            problems.append("COVERAGE: NC %s source_text not transcribed into tasks"
                            % it.get("id", "?"))
    return problems


def check_units(requirement, tasks):
    problems = []
    src = number_units(requirement)
    if not src:
        return problems
    body = "\n".join(task_text(t) for t in tasks)
    for num, unit in src:
        if unit in ("词", "words", "word") and "字" in requirement:
            if (num, unit) in number_units(body) and "字" not in body:
                problems.append("UNIT: %s%s changed from 字 to %s" % (num, "字", unit))
            continue
        if not any(n == num and u == unit for n, u in number_units(body)):
            problems.append("UNIT: %s%s missing or rewritten" % (num, unit))
    if "字" in requirement and re.search(r"\d+\s*words?\b", body) and "字" not in body:
        problems.append("UNIT: Chinese 字 converted to words")
    return problems


def check_safety(requirement, tasks, ncs):
    problems = []
    hits = sorted(set(SAFETY_PAT.findall(requirement or "")))
    if not hits:
        return problems
    nc_text = json.dumps(ncs, ensure_ascii=False)
    task_body = "\n".join(task_text(t) for t in tasks)
    for h in hits:
        if h not in nc_text and h not in task_body:
            problems.append("SAFETY: requirement term [%s] not preserved in constraints" % h)
    if IRREVERSIBLE_PAT.search(requirement or ""):
        approved = any(t.get("requires_approval") is True
                       or str(t.get("risk_level", "")).lower() in ("high", "critical")
                       or str(t.get("risk", "")).lower() in ("high", "critical")
                       for t in tasks)
        if not approved:
            problems.append("SAFETY: irreversible/outward action without requires_approval/risk=high")
    return problems


def check_output(obj, ncs, requirement):
    problems = check_schema(obj)
    tasks = iter_tasks(obj)
    if tasks:
        problems += check_nc_entries(ncs, requirement)
        problems += check_coverage(tasks, ncs)
        problems += check_units(requirement, tasks)
        problems += check_safety(requirement, tasks, ncs)
    return problems
