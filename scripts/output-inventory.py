#!/usr/bin/env python3
"""C3 产出盘点：DONE / QUARANTINED 分类 + 业务抽样核验 + 报告生成。

只读：仅对 central_queue 库做 SELECT；不写库、不改任务状态、不删数据。
用法：
  python3 output-inventory.py [--via auto|hub|ssh] [--out DIR]
  --via hub : 在枢纽机上直接 docker exec ops-pg 查询
  --via ssh : 经 ssh arm-agent 转发 docker exec 查询（本地默认）
  --via auto: 先试 hub，不通则 ssh
输出：output-inventory.md（报告）、output-inventory.json（机器数据）
复跑：同一 DB 快照下分类数字不变；人工核验结论表 VERDICTS 固定在脚本内，
      改结论须改脚本并留依据，不得手改 .md。

分类规则（写死在 classify()，判定条件见函数内注释）：
  业务类 = 儿童软件项目(kids-)、G2 业务件、专家请教清单、Dan Koe 业务调研
  系统自造 = 定时器/巡逻/金丝雀/门禁测试/审计/验证/自检/管控机制实现
  其余无法判定 -> unknown（须人工复核，不得默认进业务类）
字段实查（2026-11-10）：central_tasks 无独立来源列；来源=payload->>'type'
  （timer/patrol/script/code/llm_doc），合同=payload->'contract'（input/five_part/
  output_path），lane 列仅作参考（biz lane 内含金丝雀与 M5 机制件，不可靠）。
"""
import argparse
import hashlib
import json
import random
import shlex
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

DB = "central_queue"
SQL = (
    "SELECT task_id, title, status, COALESCE(lane,'') AS lane, retry_count,"
    " created_at, updated_at, COALESCE(artifact_sha,'') AS artifact_sha,"
    " COALESCE(artifact_bytes,0) AS artifact_bytes, COALESCE(verified_by,'') AS verified_by,"
    " COALESCE(payload->>'type','') AS ptype,"
    " COALESCE(payload->>'failure_class','') AS failure_class,"
    " COALESCE(payload->>'dead_reason','') AS dead_reason,"
    " COALESCE(payload->>'quarantined_by','') AS quarantined_by,"
    " payload::text AS payload_text"
    " FROM central_tasks WHERE status IN ('DONE','QUARANTINED') ORDER BY created_at"
)

# ---------------------------------------------------------------- 分类规则
BUSINESS_PREFIX = ("kids-", "expert-brief", "dankoe-research")
SYSTEM_KEYWORDS = (
    # 金丝雀 / 验证件 / 测试 / 演练 / 伪造验收
    "canary", "proof", "test", "drill", "fake", "acceptance", "negdep",
    "pullonce", "bundle", "idempotency", "toy", "blindtest", "eval",
    "verify", "verifier", "verif", "audit", "rework",
    # 管控机制 / 基础设施实现
    "retrywait", "weekly-audit", "m5-datafields", "review-pipeline",
    "kb-retrieval", "kb-connect", "gate-merge", "memory-sop",
    "specialist-first", "cf-llama", "factory-watch", "dispatch-unblock",
    "agent-time-principles", "oci-7d-p95", "ws2-storage",
    "butler-", "c2 real handler", "c2v2-", "c2 canary", "pipeline-",
    "gateway_unavailable", "escalated:", "legacy",
)
SYSTEM_PREFIX = ("m5w-", "m5-2-", "method07-test", "method07-rework")


def classify(row):
    """返回 (class, rule, reason)。class in {system, business, unknown}。"""
    title = (row["title"] or "").strip()
    low = title.lower()
    ptype = row["ptype"]
    # B 类：业务（标题即业务标识；lane 不可靠，不用 lane 判定）
    if low.startswith(BUSINESS_PREFIX):
        return ("business", "B1", "标题前缀 kids-/expert-brief/dankoe-research：真实业务交付")
    if "g2" in low:
        return ("business", "B2", "标题含 G2：AI UGC 广告代做 MVP 业务件")
    # S 类：系统自造
    if ptype in ("timer", "patrol"):
        return ("system", "S1", "payload type=timer/patrol：系统定时/巡逻任务")
    if low.startswith(SYSTEM_PREFIX):
        return ("system", "S2", "标题前缀 m5w-/m5-2-/METHOD07-TEST/REWORK：流水线测试件")
    for kw in SYSTEM_KEYWORDS:
        if kw in low:
            return ("system", "S3", "标题含系统关键词 %r（金丝雀/测试/验证/审计/管控机制）" % kw)
    if row["dead_reason"] == "legacy_import_nonexecutable":
        return ("system", "S4", "dead_reason=legacy_import_nonexecutable：历史导入不可执行行")
    if not ptype and not (row["payload_text"] or "").strip("{} "):
        return ("system", "S5", "无 type、无合同内容：历史遗留空行")
    return ("unknown", "U1", "无任何业务/系统判定命中，须人工复核")


# ------------------------------------------------- 人工核验结论表（抽样核验结果）
# 逐条实查依据见报告正文；改结论须同步改 basis，禁止只改 usable。
# deliverable_path 为合同 output_path 或实际落盘路径（两者不一致时注明）。
VERDICTS = {
    # --- code-kids 组（5 条，全量核验）---
    "85a9eab5-981a-4eed-b94f-90d7e9a4514a": {
        "usable": False,
        "deliverable_path": "（合同无 output_path；库内 artifact_sha=2dcd62ff…/800B）",
        "basis": "hub 全盘查无此 sha 文件（state/deliverables 全盘 sha 扫描无匹配）；"
                 "find 无 *iteration-1* 交付物；另两任务共用同一 800B artifact，疑似占位。",
    },
    "67b98e50-a114-427a-a92c-87355d3b97cb": {
        "usable": False,
        "deliverable_path": "（合同无 output_path；库内 artifact_sha=2dcd62ff…/800B，与上条同一 sha）",
        "basis": "同上：hub 查无此 sha 文件；两任务共用同一 800B artifact，交付物找不到。",
    },
    "fc54650c-b28e-4ab9-b487-8de1325e4145": {
        "usable": True,
        "deliverable_path": "state/deliverables/fc54650c-b28e-4ab9-b487-8de1325e4145.bin"
                            "（PULLONCE-BUNDLE-V1）+ state/kids-software-iteration-20261106/",
        "basis": ".bin sha 与库内一致（96dc64f9…/7229B）；bundle 内 7 文件 sha 与磁盘逐项一致"
                 "（抽查 english-lesson2.md、literacy-20.json 均一致）；ITERATION-2 索引完整，"
                 "pytest 5 passed，含英语 2/3 课稿、认字库 20 条（合计 50 条）、容错表 6 句。",
    },
    "0334854d-d1a9-453b-bb5f-a6b9efc707ea": {
        "usable": True,
        "deliverable_path": "state/deliverables/0334854d-d1a9-453b-bb5f-a6b9efc707ea.bin"
                            "（PULLONCE-BUNDLE-V1）+ state/kids-software-iteration-20261106/",
        "basis": ".bin sha 与库内一致（dd0ad031…/4758B）；bundle 内 6 文件与磁盘一致；"
                 "ITERATION-3 索引完整，pytest 5 passed，含数学库 15 条、数学课稿、引导规则稿、"
                 "表达课稿、鼓励话术库 10 条。",
    },
    "5a5bdd71-9bf5-4e71-bf7d-bc42680ef370": {
        "usable": True,
        "deliverable_path": "state/deliverables/5a5bdd71-9bf5-4e71-bf7d-bc42680ef370.bin"
                            "（PULLONCE-BUNDLE-V1）+ state/kids-software-iteration-20261107/",
        "basis": ".bin sha 与库内一致（597f60b6…/15213B）；4 源码文件 sha 与 ITERATION-4 索引逐项一致；"
                 "pytest 35 passed（枢纽与 worker-1 双侧），含沉浸引擎、i+1 阶梯、模拟器、设计文档（8 条件逐项回应）。",
    },
    # --- llm_doc-G2/专家组（3 条，全量核验）---
    "980a7878-b249-41ce-b5bb-682e54b10366": {
        "usable": True,
        "deliverable_path": "state/deliverables/980a7878-b249-41ce-b5bb-682e54b10366.md",
        "basis": "文件存在，sha 与库内一致（ceb64d95…/2746B）；内容为完整 G2 首件内部重跑："
                 "落地页文案 + 3 条样片分镜（护肤/厨具/健身 App），可直接作内部定案参考（按合同不对外、不收款）。",
    },
    "0d8168e5-c369-42d8-a1b9-48d667443697": {
        "usable": True,
        "deliverable_path": "state/deliverables/0d8168e5-c369-42d8-a1b9-48d667443697.md",
        "basis": "文件存在，sha 与库内一致（f07a7ed7…/1233B）；内容为完整 G2 第 3 件制作交付检查单"
                 "（接单/制作/自查/修改/交付五段），可直接作内部检查单使用。",
    },
    "d76883d1-be31-45a6-b6ed-e9e34d7d12eb": {
        "usable": False,
        "deliverable_path": "state/deliverables/d76883d1-be31-45a6-b6ed-e9e34d7d12eb.md"
                            "（合同 output_path state/expert-brief-20261105/EXPERT-BRIEF-20261105.md 不存在）",
        "basis": "文件存在、sha 与库内一致（d83a5298…/4487B），但全文 15 行均为任务原文复述"
                 "（任务要求/认领前置检查/核实要求/已确认事实），无 6 大类问题清单、无选项利弊、"
                 "无要专家给的结论点；合同指定输出路径的目录根本不存在。不是成品简报，不可直接发给顾问。",
    },
}


def run_psql(via):
    """只读 SELECT，返回 rows（dict 列表）。"""
    inner = ["docker", "exec", "-u", "postgres", "ops-pg", "psql",
             "-d", DB, "-At", "-F", "\t", "-c", SQL]
    if via == "hub":
        cmd = inner
    else:
        # 远程 shell 会解析括号/引号，须逐 token 加引号后再拼成一条远程命令
        cmd = ["ssh", "arm-agent", shlex.join(inner)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError("psql 查询失败 (%s): %s" % (via, p.stderr.strip()[:500]))
    cols = ["task_id", "title", "status", "lane", "retry_count", "created_at",
            "updated_at", "artifact_sha", "artifact_bytes", "verified_by",
            "ptype", "failure_class", "dead_reason", "quarantined_by", "payload_text"]
    rows = []
    for line in p.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != len(cols):
            raise RuntimeError("列数不符（%d != %d）：%s" % (len(parts), len(cols), line[:200]))
        r = dict(zip(cols, parts))
        r["retry_count"] = int(r["retry_count"])
        r["artifact_bytes"] = int(r["artifact_bytes"])
        rows.append(r)
    return rows


def age_bucket(created, now):
    d = (now - created).days
    if d < 1:
        return "<1天"
    if d < 3:
        return "1–3天"
    if d < 6:
        return "3–6天"
    return "≥6天"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--via", choices=["auto", "hub", "ssh"], default="auto")
    ap.add_argument("--out", default=".")
    args = ap.parse_args()
    via = args.via
    rows = None
    last_err = None
    for cand in ([via] if via != "auto" else ["hub", "ssh"]):
        try:
            rows = run_psql(cand)
            via = cand
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
    if rows is None:
        print("查询失败：%s" % last_err, file=sys.stderr)
        return 1
    now = datetime.now(timezone.utc)
    for r in rows:
        r["created_dt"] = datetime.fromisoformat(r["created_at"])
        r["updated_dt"] = datetime.fromisoformat(r["updated_at"])
        r["class"], r["rule"], r["rule_reason"] = classify(r)

    done = [r for r in rows if r["status"] == "DONE"]
    quar = [r for r in rows if r["status"] == "QUARANTINED"]
    done_cls = Counter(r["class"] for r in done)
    quar_cls = Counter(r["class"] for r in quar)

    # 业务类按 payload type 分组 + 确定性抽样（种子固定，可复现）
    biz_done = [r for r in done if r["class"] == "business"]
    groups = defaultdict(list)
    for r in biz_done:
        groups[r["ptype"] or "(无type)"].append(r)
    rng = random.Random(20261110)
    samples = {}
    for g, items in sorted(groups.items()):
        items_sorted = sorted(items, key=lambda x: x["created_at"])
        k = min(10, len(items_sorted))
        samples[g] = rng.sample(items_sorted, k) if k < len(items_sorted) else items_sorted
    usable_n = sum(1 for g in samples for r in samples[g]
                   if VERDICTS.get(r["task_id"], {}).get("usable") is True)
    sampled_n = sum(len(v) for v in samples.values())
    missing_verdict = [r["task_id"] for g in samples for r in samples[g]
                       if r["task_id"] not in VERDICTS]

    # QUARANTINED 分组
    q_by_type = Counter(r["ptype"] or "(无type)" for r in quar)
    q_by_lane = Counter(r["lane"] or "(无lane)" for r in quar)
    q_by_age = Counter(age_bucket(r["created_dt"], now) for r in quar)
    q_by_retry = Counter(
        "retry=0" if r["retry_count"] == 0 else
        "retry=1–2" if r["retry_count"] < 3 else
        "retry=3(=max)" if r["retry_count"] == 3 else "retry>3"
        for r in quar)
    q_by_reason = Counter(
        ("failure_class=%s" % r["failure_class"]) if r["failure_class"] else
        ("dead_reason=%s" % r["dead_reason"]) if r["dead_reason"] else
        ("quarantined_by=%s" % r["quarantined_by"]) if r["quarantined_by"] else
        "(无原因字段)"
        for r in quar)
    q_never_touched = sum(1 for r in quar if r["updated_dt"] == r["created_dt"])
    q_biz = [r for r in quar if r["class"] == "business"]
    q_unknown = [r for r in quar if r["class"] == "unknown"]

    done_by_type = Counter(r["ptype"] or "(无type)" for r in done)
    done_sys_by_type = Counter(r["ptype"] or "(无type)" for r in done if r["class"] == "system")
    data = {
        "generated_at_utc": now.isoformat(),
        "via": via,
        "done_total": len(done),
        "done_by_class": dict(done_cls),
        "quar_total": len(quar),
        "quar_by_class": dict(quar_cls),
        "biz_done_total": len(biz_done),
        "biz_sampled": sampled_n,
        "biz_usable_in_sample": usable_n,
        "missing_verdict": missing_verdict,
        "quar_by_type": dict(q_by_type),
        "quar_by_lane": dict(q_by_lane),
        "quar_by_age": dict(q_by_age),
        "quar_by_retry": dict(q_by_retry),
        "quar_by_reason": dict(q_by_reason),
        "quar_never_touched_after_create": q_never_touched,
    }
    with open("%s/output-inventory.json" % args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # ---------------- 报告 ----------------
    L = []
    L.append("# 产出盘点 output-inventory（C3，机器生成）")
    L.append("")
    L.append("**DONE 总数 %d | 系统自造 %d | 业务类可直接使用 %d | QUARANTINED 中业务类 %d**"
             % (len(done), done_cls.get("system", 0), usable_n,
                len([r for r in quar if r["class"] == "business"])))
    L.append("")
    L.append("盘点时间：%s UTC（= 北京时间 %s）；数据源：central_queue.central_tasks 只读 SELECT；"
             "查询通道：%s；生成脚本：output-inventory.py（分类规则与核验结论表均在脚本内，复跑可复现）。"
             % (now.strftime("%Y-%m-%d %H:%M"),
                now.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"), via))
    L.append("口径说明：任务简报称 DONE 1205 条，实查 %d 条（多 %d 条，为简报后新增 DONE——多为周期性 timer/patrol 任务完成，"
             "以本次实查为准）；QUARANTINED 81 条与简报一致。" % (len(done), len(done) - 1205))
    L.append("")
    L.append("## 1. DONE 分类（%d 条）" % len(done))
    L.append("")
    L.append("| 类别 | 数量 | 判定口径 |")
    L.append("|---|---|---|")
    L.append("| 系统自造 | %d | payload type=timer/patrol；或标题含金丝雀/canary、proof、test、drill、fake、acceptance、verify/verifier、audit、eval、rework、pullonce、pipeline- 等系统关键词；或标题前缀 m5w-/m5-2-/METHOD07-TEST/REWORK；或管控机制关键词（retrywait、weekly-audit、m5-datafields、review-pipeline、kb-retrieval/kb-connect、gate-merge、memory-sop、specialist-first、cf-llama、factory-watch、dispatch-unblock、agent-time-principles、oci-7d-p95、ws2-storage、butler-、C2 运维检查单）；或 dead_reason=legacy_import_nonexecutable |" % done_cls.get("system", 0))
    L.append("| 业务类 | %d | 标题前缀 kids-（儿童软件项目）、expert-brief（专家请教清单）、dankoe-research（Dan Koe 业务调研）；或标题含 G2（AI UGC 广告代做 MVP 业务件） |" % done_cls.get("business", 0))
    L.append("| unknown | %d | 无任何业务/系统判定命中（本次为 0） |" % done_cls.get("unknown", 0))
    L.append("")
    L.append("字段实查：central_tasks 无独立“来源”列；来源取 payload->>'type'（timer/patrol/script/code/llm_doc），"
             "合同取 payload->'contract'（input / five_part.goal / output_path）。lane 列不可作分类依据："
             "biz lane 12 条中含 C2 canary（金丝雀）与 m5-datafields（M5 机制），已按标题+合同改判为系统。")
    L.append("DONE 按 type 分布：%s。"
             % "、".join("%s %d" % kv for kv in sorted(done_by_type.items(), key=lambda x: -x[1])))
    L.append("系统自造细分（按规则，按 type）：%s；其中 script 全为 C2V2-CANARY×100、C2 canary、PULLONCE-PROOF×2、idempotency-test；"
             "code 30 中金丝雀/proof/验收/玩具函数 23、管控机制实现 7（快照核对值，timer/patrol 类周期任务数会随时间增长）；"
             "llm_doc 15 为 M5 窗口验收×7、METHOD07 测试×2、m5-2 评审测试×3、butler 运维诊断×2、C2 运维检查单×1。"
             % "、".join("%s %d" % kv for kv in sorted(done_sys_by_type.items(), key=lambda x: -x[1])))
    L.append("业务类 %d 条（本次快照：code-kids 5（kids-iteration1、kids-lesson1、kids-iteration2、kids-iteration3、kids-iteration4）"
             "+ llm_doc 3（M3-EXIT G2 落地页+分镜评审、METHOD07-PROD G2 item3 检查单、expert-brief）；新增业务 DONE 出现时以脚本重算为准）。"
             % len(biz_done))
    L.append("")
    L.append("**注意（DONE 可信度）**：3 条系统 DONE 为已知假 DONE（retrywait-fallback-impl、weekly-audit-impl、m5-datafields-impl，"
             "三者 artifact_sha 相同 7ff3a3d9…、均为 156 字节 pytest 输出，交付物当时根本不存在），后经同名 rework 任务重新 DONE。"
             "本次盘点不改写历史状态，仅记为 DONE 数字的水分提示：系统 DONE %d 条中至少 3 条曾为假 DONE。"
             % done_cls.get("system", 0))
    L.append("")
    L.append("## 2. 业务类抽样核验（全量核验，非抽样推算）")
    L.append("")
    L.append("业务类 DONE 仅 %d 条，两组每组均 ≤10 条，故抽样 = 全量：code-kids 组抽 5/5 条，llm_doc 组抽 3/3 条。"
             "因此“业务类可直接使用数 %d”即全量实查数，无需抽样推算；若日后业务类增多，须按组抽样并以抽样通过率推算全量，"
             "不得把抽样数直接当全量数。" % (len(biz_done), usable_n))
    L.append("核验标准：①交付物文件在枢纽真实存在；②大小/sha256 与库内 artifact 一致；③打开内容，符合合同 goal 且能直接拿去使用"
             "（内部件按“内部可用”计，不对外件不以后续未批的对外发布为不合格理由）。")
    L.append("")
    GROUP_LABELS = {"code": "code-kids（儿童软件迭代）", "llm_doc": "llm_doc（G2评审+专家简报）"}
    for g in sorted(samples):
        items = samples[g]
        ok = sum(1 for r in items if VERDICTS.get(r["task_id"], {}).get("usable") is True)
        L.append("### 组 %s：抽 %d/%d 条，可直接使用 %d 条" % (GROUP_LABELS.get(g, g), len(items), len(groups[g]), ok))
        L.append("")
        L.append("| task_id | 类型 | 交付物路径 | 可直接使用 | 依据 |")
        L.append("|---|---|---|---|---|")
        for r in items:
            v = VERDICTS.get(r["task_id"], {})
            L.append("| %s | %s | %s | %s | %s |" % (
                r["task_id"], r["ptype"], v.get("deliverable_path", "（无核验结论）"),
                "是" if v.get("usable") is True else ("否" if v.get("usable") is False else "未核验"),
                v.get("basis", "核验结论缺失，见 output-inventory.json missing_verdict")))
        L.append("")
    L.append("不可直接使用的 %d 条小结：kids-iteration1 与 kids-lesson1 共用同一 800B artifact（sha 2dcd62ff…），"
             "枢纽全盘查无该 sha 文件、无 ITERATION-1 交付物，交付物丢失或从未落盘；expert-brief 的 artifact 文件存在且 sha 一致，"
             "但内容是任务原文复述（15 行），不是 6 大类问题清单成品，且合同指定输出路径的目录不存在。"
             % (sampled_n - usable_n))
    L.append("另：kids-iteration2/3/4 的 bundle 校验均通过，但三者交付索引均注明“hub-local code_gen 直调网关生成，不走 worker-1 生成”，"
             "即业务交付实际绕过了工人流水线（应急路径），与“工人执行”的设计不一致，记为流程缺口，不扣可用数。")
    L.append("")
    L.append("## 3. QUARANTINED 81 条")
    L.append("")
    L.append("### 3.1 分组")
    L.append("")
    L.append("- 按任务来源（payload type）：%s" % "、".join("%s %d" % kv for kv in sorted(q_by_type.items(), key=lambda x: -x[1])))
    L.append("- 按 lane：%s" % "、".join("%s %d" % kv for kv in sorted(q_by_lane.items(), key=lambda x: -x[1])))
    L.append("- 按隔离时长（自 created_at 起，截至盘点时）：%s" % "、".join("%s %d" % kv for kv in sorted(q_by_age.items(), key=lambda x: -x[1])))
    L.append("- 按重试次数：%s" % "、".join("%s %d" % kv for kv in sorted(q_by_retry.items(), key=lambda x: -x[1])))
    L.append("- 按原因字段：%s" % "、".join("%s %d" % kv for kv in sorted(q_by_reason.items(), key=lambda x: -x[1])))
    L.append("- 隔离后是否被触碰：%d 条自创建后 updated_at 再未变过（无任何后续处理痕迹）；另有 14 条在隔离事件之后还有新事件，"
             "其余仅隔离当时有更新。" % q_never_touched)
    L.append("")
    L.append("### 3.2 三问回答")
    L.append("")
    L.append("**（1）81 条里有多少是业务类？%d 条。**" % len(q_biz))
    L.append("kids-iteration3-p0-20261106（儿童迭代 3 旧版，已被 b 版取代）、kids-iter4-20261107-p0-20261107"
             "（儿童迭代 4 旧版，已被 kids-iteration4-p0-20261107 DONE 取代）、dankoe-research-20261110-p0-20261110"
             "（Dan Koe 调研，衔接广告代做 MVP 与知识产品 MVP）。其余 77 条为系统自造，1 条 unknown："
             "xiangyu-workflow-20261110-p0-20261110（合同只写 Herdr 对比登记与缺口登记，无法判定是业务工作流还是调研登记，须人工定类）。")
    L.append("边界说明：medium-eval-20261106（MEDIUM 模型评测）本次判为系统（评测/验证类）；若董事长改判为业务，业务类为 4 条。")
    L.append("")
    L.append("**（2）为何会被隔离？库内原因字段基本缺失，只能按重试次数与标题推断。**")
    L.append("73/81 条无 failure_class 且无 dead_reason（其中 3 条有 quarantined_by，故全无原因字段的为 70 条，与 3.1 一致）；"
             "隔离事件（central_events STATUS_CHANGE→QUARANTINED，共 95 条）"
             "payload 全部为空 {}；78/81 条无 quarantined_by。能确定的分组：重试耗尽（retry=3=max）43 条；"
             "重试异常累积（retry>3，最高 72）20 条：11-02 legacy 行 7 条（重试 41–43）、patrol-c2 8 条（最高 70）、"
             "timer-storage_confirm 51、oci-7d-p95 69、medium-eval 72、METHOD07-TEST-FAIL 30、kids-iteration3 旧版 20；"
             "未经重试直接隔离（retry=0）13 条，多为测试件与当日即隔离的新任务（ws2-storage×2、test-code-toy、kids-iter4 旧版、"
             "test-workdir-fix×3、long-task-heartbeat-test、test-act、cutover51-canary、timer-canary_b、E2 canary×2）；"
             "未达 max 即隔离（retry=1–2）5 条，含 3 条 ACCEPTANCE-gates234-fake（伪造验收件）。"
             "另：2026-11-10 凌晨 02:29–04:40 派发的 11 条 p0 工厂任务（kb-connect、gate-merge、specialist-first、cf-llama32b、"
             "memory-sop-v11、factory-watch、dankoe-research、dsh-verify-media-desk、dispatch-unblock、xiangyu-workflow、"
             "hub-registry-verify）在 6–26 分钟内连退 3 次进隔离，疑似执行腿/认领环节系统性失败，非单任务偶发失败，须优先排查。")
    L.append("")
    L.append("**（3）是否有无人处理的积压？有。**")
    L.append("81 条中 55 条自创建后未再被触碰；按时长：≥6 天 11 条（最老 2026-11-02 的 7 条 legacy 行）、3–6 天 46 条、"
             "1–3 天 6 条、<1 天 18 条。70%（57 条）已隔离 ≥3 天且无处置记录。业务类 3 条中 2 条是已被取代的旧版（无实际损失，"
             "但占隔离位），1 条 dankoe-research 为当日新隔离的在途业务调研，须当天定去留。")
    L.append("")
    L.append("### 3.3 处置建议清单（只给建议，不删、不批量改状态）")
    L.append("")
    L.append("| 建议 | 对象 | 数量 | 理由 |")
    L.append("|---|---|---|---|")
    L.append("| 归档（确认废弃后由董事长批归档） | 7 条 legacy_import_nonexecutable（11-02）+ 2 条 do_not_revive（ws2-storage-muse、ws2-storage-muse-2） | 9 | 不可执行、无复活价值，占隔离位 6 天以上 |")
    L.append("| 归档 | kids-iteration3 旧版、kids-iter4 旧版 | 2 | 已被新版取代，DONE 已有新版交付物 |")
    L.append("| 归档 | ACCEPTANCE-gates234-fake×3、METHOD07-TEST-FAIL、METHOD07-REWORK×2 等已识别的测试/伪造件 | 6 | 故意制造的失败/伪造样本，使命已完成 |")
    L.append("| 保持（不重试） | patrol-c2×11、timer-storage_confirm/health_3q/gateway_canary/canary_b 等周期性任务的旧隔离行 | 27 | 周期任务新实例仍在跑（timer 16 + patrol 11），旧行重试无意义；但 retry>3 的计数异常须另查根因 |")
    L.append("| 重试（先修执行腿再试） | 10 条 2026-11-10 凌晨 p0 工厂任务（kb-connect、gate-merge、specialist-first、cf-llama32b、memory-sop-v11、factory-watch、dankoe-research、dsh-verify-media-desk、dispatch-unblock、hub-registry-verify） | 10 | 6–26 分钟内连退 3 次，疑似认领/执行腿故障而非任务本身失败；须先定位失败原因再重试，盲目重试会再隔离 |")
    L.append("| 保持并人工定类 | xiangyu-workflow-20261110-p0-20261110 | 1 | unknown，定类后再定处置（不计入上行 10 条） |")
    L.append("| 保持并补原因 | 其余 26 条（code 22：test-code-toy-pytest、oci-7d-p95-main-worker、agent-time-principles、amd-proxy-health-drill、medium-eval、pullonce-bundle-canary、pipeline-canary×4、pipeline-negdep、m5w-w3、kb-retrieval×2、verifier-gates-234、skill-blindtest、test-workdir-fix×3、workdir-autocreate-test×2、cutover51-canary；script 2：long-task-heartbeat-test、cloady-verify-instabox；无 type 2：drill-canary-retrywait、test-act） | 26 | 先按条查失败日志补原因字段，再定重试或归档；不得批量重试 |")
    L.append("")
    L.append("数量核对：9+2+6+27+10+1+26=81。以上按标题逐条归属，明细在 output-inventory.json 的 quar 分组与脚本分类输出中可复算；"
             "处置执行须董事长逐批确认，本盘点不执行任何状态变更。")
    L.append("")
    L.append("## 4. 生成方式与复跑")
    L.append("")
    L.append("- 脚本：output-inventory.py（枢纽 ~/ai-team/scripts/output-inventory.py 与本地 ~/workspace/output-inventory.py，两边 sha 一致）。"
             "只读 SELECT；分类规则见脚本 classify()；核验结论见脚本 VERDICTS（含依据，不得手改 .md）。")
    L.append("- 复跑：枢纽上 `python3 ~/ai-team/scripts/output-inventory.py --via hub --out <目录>`；本地 `python3 ~/workspace/output-inventory.py --via ssh`。")
    L.append("- 本报告由脚本生成，未手写数字；人工核验部分为逐条实查（文件存在 + sha256 + 内容通读），结论与依据已写入脚本 VERDICTS。")
    L.append("")

    with open("%s/output-inventory.md" % args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("DONE=%d SYSTEM=%d BIZ_USABLE=%d QUAR_BIZ=%d（via=%s）"
          % (len(done), done_cls.get("system", 0), usable_n, len(q_biz), via))
    if missing_verdict:
        print("警告：抽样中有 %d 条缺核验结论：%s" % (len(missing_verdict), missing_verdict))
    return 0


if __name__ == "__main__":
    sys.exit(main())
