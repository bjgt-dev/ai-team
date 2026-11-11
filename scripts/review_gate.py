#!/usr/bin/env python3
"""L3 专家评审门禁：用 verifier-stable 模型检查报告是否存在幻觉/事实错误。

用法: python3 review_gate.py <bundle_dir> <task_id>
返回: JSON {"verdict": "PASS"|"FAIL", "reasons": [...]}
"""
import json, os, sys, urllib.request

BUNDLE_DIR = sys.argv[1]
TASK_ID = sys.argv[2]

# 找待评审产物：优先契约声明的 output_file，其次 artifacts/report.md，再次 artifacts 下任一 .md
import glob as _glob
TEAM_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(BUNDLE_DIR)))
contract = {}
try:
    contract = json.load(open(os.path.join(TEAM_ROOT, "contracts", TASK_ID + ".json"), encoding="utf-8"))
except Exception:
    contract = {}
report_path = None
of = contract.get("output_file")
if of:
    cand = os.path.join(BUNDLE_DIR, of)
    if os.path.isfile(cand):
        report_path = cand
if not report_path:
    for root, _, files in os.walk(os.path.join(BUNDLE_DIR, "artifacts")):
        if "report.md" in files:
            report_path = os.path.join(root, "report.md")
            break
if not report_path:
    mds = sorted(_glob.glob(os.path.join(BUNDLE_DIR, "artifacts", "**", "*.md"), recursive=True))
    if mds:
        report_path = mds[0]

if not report_path:
    print(json.dumps({"verdict": "FAIL", "reasons": ["no reviewable artifact (.md) found"]}))
    sys.exit(0)

_cfg = {}
try:
    _cfg = json.load(open(os.path.join(TEAM_ROOT, "scripts", "review_config.json"), encoding="utf-8"))
except Exception:
    _cfg = {}
DEFAULT_MIN_CHARS = int(_cfg.get("default_min_chars", 100))

content = open(report_path, encoding="utf-8").read()
# Size rule (2026-10-20 chairman ruling): if the contract declares a
# file_min_size criterion for this deliverable, that standard governs and the
# default length floor does not apply. Only without a contract standard does
# the default floor (100 chars) apply.
_rel = os.path.relpath(report_path, BUNDLE_DIR)
_contract_min = None
for _c in contract.get("acceptance_criteria", []) or []:
    if _c.get("type") == "file_min_size" and _c.get("target") == _rel:
        _contract_min = _c.get("expected")
        break
if _contract_min is not None:
    if os.path.getsize(report_path) < int(_contract_min):
        print(json.dumps({"verdict": "FAIL", "reasons": ["below contract file_min_size %s" % _contract_min]}))
        sys.exit(0)
elif len(content) < DEFAULT_MIN_CHARS:
    print(json.dumps({"verdict": "FAIL", "reasons": ["report too short"]}))
    sys.exit(0)

# 调 newapi verifier-stable 做评审
NEWAPI_URL = "http://127.0.0.1:3001/v1/chat/completions"
# token 从环境或文件读
token = os.environ.get("NEWAPI_TOKEN", "")
if not token:
    try:
        token = open("/home/ubuntu/.newapi-token").read().strip()
    except:
        pass

prompt = f"""你是一名严格的事实核查专家。请评审以下研究报告是否存在以下问题：

1. 幻觉：编造不存在的数据、产品、公司、数据（如"2026年XX产品发布"但实际不存在）
2. 过时信息：给出明显过时的代码、API、政策（如已下线的服务还推荐）
3. 逻辑矛盾：前后文自相矛盾
4. Prompt 泄露：把系统提示词或指令写进正文

任务要求（契约 objective，供核对是否编造任务外内容）：
{contract.get("objective", "(无)")[:2000]}

报告内容：
---
{content[:8000]}
---

请只返回 JSON 格式：
{{"verdict": "PASS" 或 "FAIL", "reasons": ["具体问题1", "具体问题2"]}}

如果报告基本属实、数据合理，即使文笔一般也判 PASS。只有存在明显编造或严重事实错误时才判 FAIL。"""

try:
    req_data = json.dumps({
        "model": "verifier-stable",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 1000
    }).encode()

    req = urllib.request.Request(
        NEWAPI_URL, data=req_data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "curl/8.0"
        }
    )
    resp = urllib.request.urlopen(req, timeout=120)
    result = json.loads(resp.read())
    review_text = result["choices"][0]["message"]["content"]

    # 解析返回的 JSON：容忍免费模型的双重转义与代码围栏，实在解析不出才 FAIL
    import re
    def _parse_review(text):
        m = re.search(r'\{.*\}', text, re.DOTALL)
        cand = m.group(0) if m else text
        unescaped = cand.replace(chr(92) + chr(34), chr(34)).replace(chr(92) + "n", chr(10))
        for t in (cand, unescaped):
            try:
                return json.loads(t)
            except Exception:
                continue
        vm = re.search(r"verdict\W{1,4}(PASS|FAIL)", cand)
        if vm:
            return {"verdict": vm.group(1), "reasons": ["verdict extracted by fallback parser"]}
        return {"verdict": "FAIL", "reasons": ["review parse failed: " + text[:200]]}
    review = _parse_review(review_text)

    print(json.dumps(review, ensure_ascii=False))

except Exception as e:
    # 评审服务不可用时，保守起见判 FAIL（需人工看）
    print(json.dumps({
        "verdict": "FAIL",
        "reasons": [f"review service error: {str(e)[:200]}"]
    }))
