#!/usr/bin/env python3
# 第一层·声明式路由 route.py：任务文本 → 领域 + 应查阅的 Skill
# 先规则（关键词硬匹配），无命中再用 newapi 小模型在注册表里选；都失败则诚实返回 unknown。
# 用法：route.py "任务描述" → stdout 一行 JSON
import json, os, re, sys, urllib.request
from pathlib import Path

REG = Path("/home/ubuntu/ai-team/skills-registry/registry.json")
RULES = [
    (["排障", "故障", "报错", "404", "500", "超时", "宕机", "日志", "崩溃", "失败原因"], "ops",
     ["failure-triage", "vps-gatekeeper", "failure-isolation"]),
    (["契约", "派单", "派发", "调度", "任务拆解"], "dispatch",
     ["contract-writer", "expert-routing"]),
    (["证据", "验收", "自检", "verify", "门禁"], "verify",
     ["evidence-self-check", "evidence-bundle"]),
    (["隧道", "代理", "网络", "端口", "vpngate", "节点"], "network",
     ["network-tunnel"]),
    (["浏览器", "登录", "验证码", "cloudflare", "人机验证"], "browser",
     ["human-browser-stealth"]),
    (["选品", "闲鱼", "竞品", "定价", "商业", "市场调研", "赚钱"], "business",
     ["expert-routing"]),
    (["孩子", "儿童", "跟读", "启蒙", "教学", "养娃", "成长事件", "家长摘要", "日记", "小蛋糕"], "education",
     ["child-education"]),
    (["代码", "重构", "函数", "bug", "单元测试", "脚本"], "dev",
     ["executor-selection", "evidence-self-check"]),
    (["边界", "权限", "隔离", "沙箱"], "safety",
     ["parts-boundary", "vps-gatekeeper"]),
    (["碎碎念", "灵感", "随口", "感叹", "brain_dump", "反向提案", "深挖"], "inspiration",
     ["inspiration-radar"]),
    (["外脑", "求助", "深水区", "gemini", "人肉传话筒"], "brain",
     ["brain-trust-router"]),
    (["卡点", "未知", "调研", "搜索优先", "保活", "休眠", "掉线", "新领域", "没思路"], "method",
     ["external-resources-first", "brain-trust-router"]),
    (["交接", "切号", "接管", "换账号", "handoff", "conductor"], "handoff",
     ["architect-handoff"]),
    (["对齐", "澄清", "选择题", "模糊指令"], "clarify",
     ["requirement-clarification"]),
    (["去ai味", "流水线", "排版", "分发", "content-team", "单源多用", "矩阵"], "content",
     ["content-pipeline"]),
    (["代码审查", "审查代码", "找漏洞", "漏洞", "安全审计", "code-review", "上线前检查"], "review",
     ["code-review"]),
    (["配图", "封面", "生图", "cover", "image-generation"], "image",
     ["image-generation"]),
    (["薄工人", "工人接入", "run_cycle", "新工人", "bootstrap"], "worker",
     ["cloud-worker"]),
    (["source_tag", "防幻觉", "家长确认", "物理门禁", "gatekeeper"], "gate",
     ["gatekeeper-physics"]),
    (["轮值", "集群自举", "带头大哥", "多账号", "MUSE-SWITCH"], "cluster",
     ["cluster-bootstrap"]),
    (["二分", "bisect", "空响应", "归一化"], "debug",
     ["debug-bisect"]),
]

def by_name(skills, prefs):
    out = []
    for p in prefs:
        for s in skills:
            if p in s["name"] or p in s["path"]:
                out.append({"name": s["name"], "path": s["path"]})
                break
    return out

def llm_pick(task, skills):
    base = os.environ.get("NEWAPI_BASE", "http://127.0.0.1:3001/v1").rstrip("/")
    key = os.environ.get("NEWAPI_KEY", "")
    if not key:
        return None
    catalog = "\n".join(f"- {s['name']}: {s['description'][:120]}" for s in skills)
    prompt = ("你是任务路由器。从技能目录里为任务选最相关的 1-3 个技能。只输出 JSON："
              '{"domain":"领域英文短词","skills":["技能name",...]}，没有合适的就空数组。\n\n技能目录：\n'
              + catalog + f"\n\n任务：{task}")
    body = json.dumps({"model": "executor-primary",
                       "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": 1500}).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body,
                                 headers={"Authorization": "Bearer " + key,
                                          "Content-Type": "application/json"})
    txt = json.loads(urllib.request.urlopen(req, timeout=120).read())["choices"][0]["message"].get("content") or ""
    m = re.search(r"\{.*\}", txt, re.S)
    return json.loads(m.group(0)) if m else None

def main():
    task = sys.argv[1] if len(sys.argv) > 1 else ""
    skills = json.loads(REG.read_text())["skills"]
    scored = []
    for kws, domain, prefs in RULES:
        hit = sum(1 for k in kws if k.lower() in task.lower())
        if hit:
            scored.append((hit, domain, prefs))
    if scored:
        scored.sort(reverse=True)
        _, domain, prefs = scored[0]
        print(json.dumps({"domain": domain, "method": "rules",
                          "skills": by_name(skills, prefs)}, ensure_ascii=False))
        return
    pick = llm_pick(task, skills)
    if pick:
        chosen = [{"name": s["name"], "path": s["path"]} for s in skills if s["name"] in pick.get("skills", [])]
        print(json.dumps({"domain": pick.get("domain", "unknown"), "method": "llm",
                          "skills": chosen}, ensure_ascii=False))
        return
    print(json.dumps({"domain": "unknown", "method": "none", "skills": []}, ensure_ascii=False))

if __name__ == "__main__":
    main()
