#!/usr/bin/env python3
"""hub-local code_gen handler (令19问1选A, 2026-11-06 应急落地).

生成与执行分离: code_spec (本文件调用方给出的确定性规格) ->
code_gen (本脚本, hub-local, 直调网关 Tier-1, 只产文件)
模型输出协议 (必须遵守, 否则解析失败): 对每个文件输出
===FILE: <相对路径> ===
<文件内容, 原样, 不加任何转义, 不加 markdown 围栏>
===END===
可输出多个文件块, 块外不写任何文字. JSON 协议仅作后备.

用法:
  python3 scripts/code-gen-20261106.py --spec spec.json --out-dir state/code-gen-20261106/out/<task>
  spec.json: {"task_id": "...", "model_primary": "glm-4-flash-cc",
              "model_fallback": "qwen3.8-flash",
              "system": "...", "prompt": "...", "max_tokens": 12000, "timeout_s": 180}
输出: 写 files 到 out-dir (只写 out-dir 下的相对路径, 拒绝 ../ 与绝对路径),
      写 receipt.json (model, latency, bytes, 每文件 sha256, 无 token).
永不打印 token. 失败时 exit != 0, 不写半成品为成功.
"""
import argparse, hashlib, json, os, re, sys, time, urllib.request

OPENCODFIG = "/home/ubuntu/.config/opencode/opencode.json"
GATEWAY = "http://127.0.0.1:3001/v1"


def token():
    c = json.load(open(OPENCODFIG))
    for p in c.get("provider", {}).values():
        opt = p.get("options") or {}
        if opt.get("apiKey"):
            return opt["apiKey"]
        if p.get("apiKey"):
            return p["apiKey"]
    return ""


def call(model, system, prompt, max_tokens, timeout_s):
    t0 = time.time()
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
        "max_tokens": max_tokens, "temperature": 0}).encode()
    req = urllib.request.Request(
        GATEWAY + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + token(),
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            d = json.loads(r.read().decode() or "{}")
    except Exception as e:
        return "", getattr(e, "code", None), str(e)[:300], time.time() - t0
    ch = (d.get("choices") or [])
    msg = (ch[0].get("message") or {}) if ch else {}
    return (msg.get("content") or ""), d.get("model", ""), "", time.time() - t0


def extract_files(text):
    """Preferred protocol: ===FILE: <relpath> === ... ===END=== blocks, verbatim.
    Fallback: JSON {"files":[...]} (with escape repair). Marker protocol avoids
    JSON-escaping corruption of code (e.g. \\n becoming a real newline)."""
    m = re.findall(r"===FILE:\s*(\S+)\s*===\n(.*?)\n===END===", text, re.S)
    # ARCHFIX 2026-11-06 (m5w-w3-mul_two): if marker count exceeds regex hits,
    # a later block is missing ===END=== and would be silently dropped.
    # Fall back to split parsing of ALL blocks in that case, not only on zero hits.
    _markers = re.findall(r"===FILE:\s*\S+\s*===", text)
    if not m or len(_markers) > len(m):
        # Fallback 2026-11-06: some models emit ===FILE:=== markers with no
        # ===END=== (kids-iteration3 15:32: whole response landed in one file).
        # Split on markers instead of failing closed to raw-output.txt.
        parts = re.split(r"===FILE:\s*(\S+)\s*===\n", text)
        if len(parts) >= 3:
            _split = []
            for i in range(1, len(parts) - 1, 2):
                body = parts[i + 1]
                body = re.sub(r"\n===END===\s*$", "", body)
                _split.append((parts[i], body))
            if len(_split) >= len(_markers):
                m = _split
    if m:
        files = []
        for path, content in m:
            # Formal pipeline fix 2026-11-06: models wrap file bodies in
            # ```python ... ``` fences despite the ===FILE: protocol. A fence
            # inside a .py file is a SyntaxError on the worker (pytest exit 2,
            # canary-20261106c). Strip one leading fence line (``` or ```lang)
            # and one trailing fence line before the file is written.
            _lines = content.splitlines()
            if _lines and _lines[0].strip().startswith("```"):
                _lines = _lines[1:]
            if _lines and _lines[-1].strip() == "```":
                _lines = _lines[:-1]
            content = "\n".join(_lines) + ("\n" if _lines else "")
            files.append({"path": path.strip(), "content": content})
        return files
    return None


def extract_json(text):
    s = text.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        # strip first fence line and trailing fence
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines)
    # find outermost {...}
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in model output (len=%d)" % len(text))
    body = s[start:end + 1]
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        # Repair common model mistakes, single pass, never touching a
        # backslash that is already part of a valid \\ pair.
        fixed = re.sub(r"(?<!\\)\\'", "'", body)
        fixed = re.sub(r"(?<!\\)\\(?!['\"\\/bfnrtu\\])", r"\\\\", fixed)
        return json.loads(fixed)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    spec = json.load(open(a.spec))
    task = spec["task_id"]
    out_dir = os.path.abspath(a.out_dir)
    os.makedirs(out_dir, exist_ok=True)
    models = [spec.get("model_primary", "glm-4-flash-cc"),
              spec.get("model_fallback", "qwen3.8-flash")]
    max_tokens = int(spec.get("max_tokens", 12000))
    timeout_s = int(spec.get("timeout_s", 180))
    system = spec.get("system", "You are a code generator. Output ONLY JSON.")
    prompt = spec["prompt"]
    last_err = ""
    content = returned = ""
    used_model = ""
    lat = 0.0
    for m in models:
        content, returned, last_err, lat = call(m, system, prompt, max_tokens, timeout_s)
        if content.strip() and not last_err:
            used_model = m
            break
        print("code_gen: model %s failed: %s" % (m, last_err), file=sys.stderr)
    if not content.strip():
        print("code_gen FAILED task=%s err=%s" % (task, last_err), file=sys.stderr)
        return 1
    try:
        files = extract_files(content)
        if files is None:
            data = extract_json(content)
            files = data["files"]
        assert isinstance(files, list) and files, "files must be non-empty list"
    except Exception as e:
        with open(os.path.join(out_dir, "raw-output.txt"), "w", encoding="utf-8") as f:
            f.write(content)
        print("code_gen FAILED task=%s parse: %s (raw saved)" % (task, e), file=sys.stderr)
        return 2
    # Formal pipeline fix 2026-11-06: fail closed if a generated .py file
    # does not parse. A SyntaxError must be caught here, on the hub, not
    # 20 minutes later on worker-1 as pytest_exit_2.
    import ast as _ast
    for item in files:
        if item["path"].endswith(".py"):
            try:
                _ast.parse(item["content"], filename=item["path"])
            except SyntaxError as e:
                with open(os.path.join(out_dir, "raw-output.txt"), "w", encoding="utf-8") as f:
                    f.write(content)
                print("code_gen FAILED task=%s %s does not parse: %s (raw saved)" % (task, item["path"], e), file=sys.stderr)
                return 3
    receipt_files = []
    for item in files:
        rel = item["path"]
        assert not os.path.isabs(rel) and ".." not in rel.split("/"), "unsafe path: %s" % rel
        p = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(p) or out_dir, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(item["content"])
        sha = hashlib.sha256(item["content"].encode()).hexdigest()
        receipt_files.append({"path": rel, "bytes": len(item["content"].encode()),
                              "sha256": sha})
    receipt = {"task_id": task, "model_requested": models[0], "model_used": used_model,
               "model_returned": returned, "latency_s": round(lat, 2),
               "raw_bytes": len(content.encode()), "files": receipt_files,
               "generated_at_cst": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(os.path.join(out_dir, "receipt.json"), "w", encoding="utf-8") as f:
        json.dump(receipt, f, ensure_ascii=False, indent=2)
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
