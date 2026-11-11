#!/usr/bin/env python3
# 教训变技能（董事长令 2026-10-20）：每日扫 verify REJECT 记录，
# 同一错误签名出现 >=3 次，自动写入 skills/avoid-pitfalls/SKILL.md 并进注册表。
import json, re, glob, collections, datetime
from pathlib import Path

B = Path("/home/ubuntu/ai-team")

def sig(s):
    s = re.sub(r"[0-9a-f]{8,}", "<hex>", str(s).lower())
    s = re.sub(r"\d+", "<n>", s)
    s = re.sub(r"/[\w\-./]+", "<path>", s)
    return s.strip()[:120]

counts = collections.Counter()
examples = {}
for f in glob.glob(str(B / "logs" / "verify-*.json")):
    try:
        d = json.load(open(f))
    except Exception:
        continue
    if d.get("verdict") != "REJECT":
        continue
    task = Path(f).stem.replace("verify-", "")
    errs = list(d.get("receipt_errors", [])) + [str(c) for c in d.get("failed_criteria", [])]
    for e in errs:
        s = sig(e)
        counts[s] += 1
        examples.setdefault(s, []).append(task)

hot = {s: c for s, c in counts.items() if c >= 3}
out = B / "skills" / "avoid-pitfalls" / "SKILL.md"
today = datetime.date.today().isoformat()

if hot:
    out.parent.mkdir(parents=True, exist_ok=True)
    header = ("---\nname: avoid-pitfalls\n"
              "description: 团队反复踩过的坑（由 skill_forge 每日从 REJECT 记录自动沉淀）：写契约、产证据、交任务前先对照本清单。同类错误出现 3 次自动入册。\n"
              "---\n\n# Avoid Pitfalls（自动沉淀的教训册）\n")
    old = out.read_text() if out.exists() else header
    add = ""
    for s, c in sorted(hot.items(), key=lambda x: -x[1]):
        if s in old:
            continue
        tasks = ", ".join(sorted(set(examples.get(s, []))))
        add += "\n## 教训（" + today + " 入册，累计 " + str(c) + " 次）\n"
        add += "- 错误签名：`" + s + "`\n"
        add += "- 出现于：" + tasks + "\n"
        add += "- 规避：动手前检查对应环节；契约必须带 created_at；每个产物文件必须有 write_file 回执（含 path 与 output_sha256）。\n"
    if add:
        out.write_text(old + add)
        print("FORGED new lessons written")
    else:
        print("FORGE_OK hot signatures already recorded:", list(hot))
else:
    print("FORGE_OK no signature >= 3; top:", counts.most_common(3))
