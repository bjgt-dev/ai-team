#!/usr/bin/env python3
# 生成技能注册表（第一层·声明式路由）：扫团队 SKILL.md 的 name/description → registry.json
import json, re
from pathlib import Path

ROOTS = [
    ("/home/ubuntu/ai-team/skills", "team"),
    ("/home/ubuntu/shared/skills", "shared"),
]
OUT = Path("/home/ubuntu/ai-team/skills-registry/registry.json")

def parse(md: Path):
    text = md.read_text(errors="ignore")
    name, desc = None, ""
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    if m:
        fm = m.group(1)
        nm = re.search(r"^name:\s*(.+)$", fm, re.M)
        dm = re.search(r"^description:\s*(.+)$", fm, re.M)
        if nm:
            name = nm.group(1).strip().strip('"')
        if dm:
            desc = dm.group(1).strip().strip('"')
    if not name:
        name = md.parent.name
    if not desc:
        body = re.sub(r"^---\n.*?\n---", "", text, flags=re.S)
        para = [ln.strip() for ln in body.splitlines() if ln.strip() and not ln.startswith("#")]
        desc = para[0][:200] if para else ""
    return name, desc

skills = []
for root, scope in ROOTS:
    for md in sorted(Path(root).rglob("SKILL.md")):
        name, desc = parse(md)
        skills.append({"name": name, "description": desc, "path": str(md), "scope": scope})
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps({"skills": skills}, ensure_ascii=False, indent=2))
print(f"REGISTRY_OK {len(skills)} skills -> {OUT}")
for s in skills:
    print("-", s["name"], "|", s["description"][:60])
