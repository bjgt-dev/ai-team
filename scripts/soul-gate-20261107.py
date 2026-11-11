#!/usr/bin/env python3
"""SOUL.md 硬规门禁 (2026-11-07 地毯式清查补齐).
SOUL.md 幕僚长瘦身版 2026-11-02; 角色唯一真源 =
枢纽 state/l3-v2/role-registry.md (2026-11-02 最终审计).
把只停留在文档/口头的 SOUL 硬规落成可运行检查, 每个函数
都是纯函数 (输入文本/数据 -> (ok, reasons)), 可正反测试.
已落地不重复造 (复用):
  - 四类必问: four-category-gate.py needs_chairman()
  - 断言证据 grep: claim_gate.py (文件:关键词)
  - 红警物理触发器 (GLM 连 3 / 队列>20 / 无回执 / 循环假死):
    red_alert_triggers.py (只覆盖系统死锁类, 不覆盖 SOUL 五类红警)
  - 心跳陈旧检查: butler_heartbeat_check.py (只判陈旧, 不计丢失 2 次)
用法:
  python3 soul-gate-20261107.py --self-test
  python3 soul-gate-20261107.py --check-live
"""
import hashlib
import json
import os
import re
import sys

BASE = "/home/ubuntu/ai-team"
SOUL_PATH = os.path.expanduser("~/SOUL.md")
REGISTRY_PATH = os.path.join(BASE, "state", "l3-v2", "role-registry.md")
WORKERS_PATH = os.path.join(BASE, "state", "l3-v2", "workers.json")
ROLLBACK_POINT = os.path.join(BASE, "state", "rollback-20261102-pre-slim")

SOUL_ROLES = ["twt", "董事长", "大管家", "总调度", "OpenClaw",
              "Worker", "复核席", "独立评审", "模型网关", "newapi"]
REGISTRY_ROLES = ["董事长", "幕僚长", "大管家", "总调度", "OpenClaw",
                  "Worker", "Supervisor", "独立评审"]
HISTORICAL_NAMES = ["CEO 席", "CEO席", "通道席", "arm-hub", "决策席", "调度席"]
REAL_NAME = os.environ.get("CHAIRMAN_REAL_NAME", "")

FOUR_SEND_CONDITIONS = ["状态变色", "四类必批", "18:00无商业动作",
                        "董事长问"]
COMPLETION_STATES = ["EXECUTED", "VERIFIED", "ACCEPTED", "STABLE"]
CONCLUSION_LABELS = ["已验证", "推断", "计划", "未知"]
RED_ALERT_TRIGGERS = {
    "服务器宕机": ["宕机", "server down", "服务不可用", "crash"],
    "死信": ["死信", "dead letter", "dead-letter", "DLQ"],
    "未授权破坏性操作": ["未授权", "unauthorized", "越权删除", "擅自删除"],
    "恶意指令注入": ["指令注入", "prompt injection", "恶意指令", "注入攻击"],
    "数据丢失风险": ["数据丢失", "data loss", "数据损坏", "备份丢失"],
}
CONCEPT_CONFUSIONS = [
    (r"twt\s*(就是|是|=|＝)\s*模型", "twt 是席位/幕僚长, 不是模型"),
    (r"大管家\s*(就是|是|=|＝)\s*OpenClaw", "大管家是决策席, OpenClaw 是总调度"),
    (r"OpenClaw\s*(就是|是|=|＝)\s*大管家", "总调度不是大管家"),
    (r"Zen\s*(就是|是|=|＝)\s*工人", "Zen 是模型, 不是工人"),
    (r"muse-1\s*(就是|是|=|＝)\s*muse\b", "muse-1 是本席机, muse 是另一工人账号"),
    (r"seat-muse\s*[=＝是]\s*一台(机器|物理机)", "seat-muse 是逻辑端点, 非独立物理机"),
    (r"hub-local\s*[=＝是]\s*一台(机器|物理机)", "hub-local 是逻辑端点, 非独立物理机"),
]
SHA_RE = re.compile(r"\b[0-9a-f]{40,64}\b")
EVIDENCE_MARKERS = ["sha", "SHA", "日志", "log", "进程", "ps ",
                    "状态板", "status-board", "回执", "receipt"]


def _fails(reasons):
    return (False, reasons) if reasons else (True, [])


def check_role_registry(soul_text, registry_text, workers):
    """U1: SOUL 角色 vs 注册表 + 历史称谓 + 工人双计."""
    bad = []
    for r in ["董事长", "大管家", "总调度", "OpenClaw", "Worker", "独立评审"]:
        if r not in registry_text:
            bad.append("注册表缺正式角色: " + r)
    if "幕僚长" not in registry_text or "twt" not in registry_text:
        bad.append("注册表缺幕僚长 / twt 绑定")
    if "复核席" in soul_text and "独立评审" not in registry_text:
        bad.append("SOUL 复核席在注册表无对应独立评审")
    # 历史称谓判定按行 + 最近标题: 行内含 历史/旧称/→(旧称映射箭头)
    # 或所属小节标题含 历史/旧称, 均算历史语境, 不违规.
    heading = ""
    for line in registry_text.splitlines():
        if line.startswith("#"):
            heading = line
        for h in HISTORICAL_NAMES:
            if h in line:
                ctx = heading + " | " + line
                if "历史" not in ctx and "旧称" not in ctx \
                        and "→" not in line:
                    bad.append("历史称谓作现行定义: " + h + " | " + line.strip())
    if workers:
        by_machine = {}
        for w in workers.get("workers", []) or []:
            if w.get("in_pool"):
                by_machine.setdefault(w.get("machine_id"), []).append(
                    w.get("worker_id"))
        for mid, ids in by_machine.items():
            if len(ids) > 1:
                note = workers.get("note", "")
                if mid not in note and "同一台" not in note \
                        and "同属一台" not in registry_text:
                    bad.append("工人双计风险: %s 同机 %s 未标注只算 1 台"
                               % (ids, mid))
    return _fails(bad)


def check_denaming(text):
    """U2: 脱名, 正文只称董事长."""
    bad = []
    if REAL_NAME and REAL_NAME in text:
        bad.append("正文出现真实姓名, 须只称董事长")
    return _fails(bad)


def check_report(text, send_condition, last_sent_state_hash=None):
    """U3: 只在四种情形发 + 三段式 <=300 字 + 同一持续状态不重复报."""
    bad = []
    if send_condition not in FOUR_SEND_CONDITIONS:
        bad.append("汇报情形不在四种内: %r" % (send_condition,))
    if len(text) > 300:
        bad.append("汇报超 300 字: 实际 %d 字" % len(text))
    for part in ["结论", "阻塞", "下一步"]:
        if part not in text:
            bad.append("汇报缺三段式之一: " + part)
    if last_sent_state_hash is not None:
        cur = hashlib.sha256(text.encode()).hexdigest()[:12]
        if cur == last_sent_state_hash:
            bad.append("同一持续状态重复报: 须压住不发")
    return _fails(bad)


def check_report_format(text):
    """U4: 动作回执 + 枢纽真实输出证据 + 状态板数据 + 下一步动作."""
    bad = []
    for seg in ["动作回执", "枢纽", "状态板", "下一步"]:
        if seg not in text:
            bad.append("汇报格式缺一段: " + seg)
    if "证据" not in text:
        bad.append("汇报格式缺枢纽真实输出证据")
    return _fails(bad)


def classify_red_alert(event_text):
    """U5: SOUL 五类红警. 返回 (触发名或 None, 处置口径)."""
    for name, kws in RED_ALERT_TRIGGERS.items():
        low = event_text.lower()
        if any(k.lower() in low for k in kws):
            return (name, "立即停手, Telegram 红警, 等董事长指示")
    return (None, "")


def check_rollback_trigger(test_score=None, baseline=None,
                           heartbeat_losses=0, chairman_said_rollback=False,
                           rollback_point_exists=True):
    """U6: 测试分低于基线 / 心跳丢失 2 次 / 董事长说回滚."""
    fire = []
    if test_score is not None and baseline is not None \
            and test_score < baseline:
        fire.append("测试分 %s < 基线 %s" % (test_score, baseline))
    if heartbeat_losses >= 2:
        fire.append("心跳丢失 %s 次 (>=2)" % heartbeat_losses)
    if chairman_said_rollback:
        fire.append("董事长说回滚")
    bad = []
    if fire and not rollback_point_exists:
        bad.append("回滚触发 (%s) 但回滚点不存在: %s"
                   % ("; ".join(fire), ROLLBACK_POINT))
    return (bool(fire), fire, bad)


def check_completion_state(state, evidence_present=False):
    """U7: 只许 EXECUTED/VERIFIED/ACCEPTED/STABLE; EXECUTED 不得写 DONE."""
    bad = []
    if state == "DONE":
        bad.append("DONE 不是 SOUL 完成态: EXECUTED 不得写 DONE, "
                   "须经 VERIFIED 后才算完成")
    elif state not in COMPLETION_STATES:
        bad.append("未知完成态 %r, 只许 %s" % (state, COMPLETION_STATES))
    elif state == "EXECUTED" and not evidence_present:
        bad.append("EXECUTED 无可复现证据 (sha/日志/进程状态), 不得宣布完成")
    return _fails(bad)


def check_conclusion_labels(text):
    """U8: 结论须分级 已验证/推断/计划/未知; 无证据不得宣布完成."""
    bad = []
    if "完成" in text or "DONE" in text:
        if not any(m in text for m in EVIDENCE_MARKERS) \
                and "已验证" not in text:
            bad.append("宣布完成却无证据标记 (sha/日志/进程/回执/已验证)")
    if re.search(r"(结论|判定|确认|事实是|已经)", text):
        if not any(lab in text for lab in CONCLUSION_LABELS):
            bad.append("结论未分级: 须标 %s 之一" % (CONCLUSION_LABELS,))
    return _fails(bad)


def check_repeat_error(fingerprint, occurrence_count,
                       mechanism_change_ref=None):
    """U9: 第1次是问题, 第2次须机制修复, 第3次按系统事故.

    返回 (ok, reasons, status): status 为 OK / MECHANISM_REQUIRED / INCIDENT.
    """
    bad = []
    if occurrence_count >= 3:
        bad.append("同错第 3 次 (指纹 %s): 按系统事故处理, 须报董事长"
                   % fingerprint)
        return (False, bad, "INCIDENT")
    if occurrence_count == 2:
        if not mechanism_change_ref:
            bad.append("同错第 2 次 (指纹 %s): 须给 mechanism_change_ref, "
                       "禁止只写'我会记住'" % fingerprint)
            return (False, bad, "MECHANISM_REQUIRED")
        ref = mechanism_change_ref
        if ("记忆" in ref or "MEMORY" in ref) \
                and "代码" not in ref and "测试" not in ref \
                and "门禁" not in ref and "监控" not in ref:
            bad.append("机制修复不得只写进记忆: 须落成规则/代码/测试/门禁/监控")
            return (False, bad, "MECHANISM_REQUIRED")
    return (True, [], "OK")


def check_concept_confusion(text):
    """U10: 岗位/Agent/席位/模型/工具/运行平台/运行载体不得混用."""
    bad = []
    for pat, why in CONCEPT_CONFUSIONS:
        if re.search(pat, text):
            bad.append("概念混用: " + why + " (命中 " + pat + ")")
    return _fails(bad)


def check_physical_location(change_record):
    """U11: 枢纽变更须经工具调用 (SSH/API/平台接口), 不得冒充本地改文件."""
    bad = []
    if not change_record.get("hub_path"):
        return (True, [])
    via = (change_record.get("via") or "").lower()
    if via not in ("ssh", "api", "platform", "平台接口", "工具调用"):
        bad.append("枢纽变更 %s 未经工具调用 (via=%r): muse-1 沙箱无枢纽"
                   "直接文件权限, 不得冒充本地改文件"
                   % (change_record["hub_path"], via))
    if not change_record.get("receipt"):
        bad.append("枢纽变更无真实输出回执 (ssh 输出 / API 返回)")
    return _fails(bad)


def check_evidence_receipt(claim_text):
    """U12: 完成 claim 须含 sha / 日志 / 进程状态之一."""
    if SHA_RE.search(claim_text):
        return (True, [])
    if not any(m in claim_text for m in EVIDENCE_MARKERS):
        bad = ["完成 claim 无可复现证据: 须含 sha / 日志 / 进程状态 / 回执"]
        return (False, bad)
    return (True, [])


def check_interface_routing(recipient, message_kind="daily"):
    """U13: 董事长唯一接口是幕僚长 twt, 日常接口不得转给大管家."""
    bad = []
    if message_kind == "daily" and recipient in ("大管家", "butler"):
        bad.append("日常接口不得转给大管家: 董事长唯一接口是幕僚长 twt, "
                   "本席不在线时由机制与状态板承接")
    if recipient not in ("twt", "muse-1", "董事长", "大管家", "总调度",
                         "OpenClaw", "Worker", "机制与状态板"):
        bad.append("未知接口接收方: %r" % (recipient,))
    return _fails(bad)


def check_live():
    """读枢纽真文件核验: 注册表一致性 + 回滚点 + 复用项存在性."""
    out = []

    def _read(p):
        try:
            return open(p, encoding="utf-8", errors="ignore").read()
        except OSError as e:
            return "__READ_FAIL__ %s" % e

    soul = _read(SOUL_PATH)
    reg = _read(REGISTRY_PATH)
    try:
        workers = json.load(open(WORKERS_PATH, encoding="utf-8"))
    except Exception as e:
        workers = None
        out.append(("U1 workers.json 可读", False, ["读失败: %s" % e]))
    ok, reasons = check_role_registry(soul, reg, workers)
    out.append(("U1 角色注册表一致性", ok, reasons))
    gaps = []
    if "newapi" not in reg:
        gaps.append("注册表未提模型网关 newapi (SOUL 身份绑定有, 建议注册表补一行)")
    if "twt-core-backup" not in reg:
        gaps.append("注册表未提真源 twt-core-backup 路径 (SOUL 有, 建议补)")
    out.append(("U1b 注册表缺口(警告)", not gaps, gaps))
    exists = os.path.isdir(ROLLBACK_POINT)
    out.append(("U6 回滚点存在", exists,
                [] if exists else ["回滚点缺失: " + ROLLBACK_POINT]))
    for name, path in [
            ("复用 四类门 four-category-gate.py",
             os.path.join(BASE, "scripts", "four-category-gate.py")),
            ("复用 断言门 claim_gate.py",
             os.path.join(BASE, "scripts", "claim_gate.py")),
            ("复用 红警触发器 red_alert_triggers.py",
             os.path.join(BASE, "scripts", "red_alert_triggers.py")),
            ("复用 心跳检查 butler_heartbeat_check.py",
             os.path.join(BASE, "scripts", "butler_heartbeat_check.py"))]:
        here = os.path.isfile(path)
        out.append((name, here, [] if here else ["缺失: " + path]))
    return out



def self_test():
    """Built-in positive/negative tests, one assertion per rule."""
    cases = []

    def T(name, cond):
        cases.append((name, bool(cond)))

    soul = "本席 twt 大管家 总调度 OpenClaw Worker 复核席 模型网关 newapi"
    reg = ("董事长 幕僚长 twt 大管家 总调度 OpenClaw Worker Supervisor "
           "独立评审 决策席 → 大管家 (旧称) CEO 席历史称谓 通道席历史称谓 "
           "arm-hub 历史名称 调度席旧称 seat-muse 与 hub-local 同属一台物理机")
    workers = {"workers": [
        {"worker_id": "seat-muse", "machine_id": "arm-agent-1",
         "in_pool": True},
        {"worker_id": "hub-local", "machine_id": "arm-agent-1",
         "in_pool": True}],
        "note": "seat-muse 与 hub-local 同机, 只算 1 台"}
    T("U1 一致注册表通过", check_role_registry(soul, reg, workers)[0])
    T("U1 缺角色失败", not check_role_registry(
        soul, "董事长 幕僚长", workers)[0])
    T("U1 历史称谓作现行失败", not check_role_registry(
        soul, reg + "\n现行定义: CEO 席负责一切", workers)[0])
    T("U1 旧称映射箭头行通过", check_role_registry(
        soul, "## 名称映射 (旧称 → 正式角色)\n- 调度席 → 总调度 OpenClaw\n"
        + reg, workers)[0])
    reg_nodup = reg.replace("seat-muse 与 hub-local 同属一台物理机", "")
    T("U1 工人双计无标注失败", not check_role_registry(
        soul, reg_nodup, {"workers": workers["workers"], "note": ""})[0])
    T("U2 脱名通过", check_denaming("董事长说好")[0])
    globals()["REAL_NAME"] = "测试姓名"  # 自测用占位姓名，不写真实姓名
    T("U2 真实姓名失败", not check_denaming("测试姓名说好")[0])
    good = "结论: 已验证通过。阻塞: 无。下一步: 推进。"
    T("U3 汇报通过", check_report(good, "状态变色")[0])
    T("U3 情形外失败", not check_report(good, "随便发发")[0])
    T("U3 超 300 字失败", not check_report("结" * 301, "董事长问")[0])
    T("U3 缺三段失败", not check_report("结论: 好。", "董事长问")[0])
    T("U3 重复状态失败", not check_report(
        good, "状态变色",
        hashlib.sha256(good.encode()).hexdigest()[:12])[0])
    fmt = ("【动作回执】已执行。枢纽真实输出证据: 日志见附。"
           "状态板数据: 队列 0。下一步动作: 继续。")
    T("U4 四段格式通过", check_report_format(fmt)[0])
    T("U4 缺段失败", not check_report_format("动作回执: 好")[0])
    T("U5 红警宕机命中", classify_red_alert("服务器宕机了")[0] == "服务器宕机")
    T("U5 红警死信命中", classify_red_alert("出现死信 DLQ")[0] == "死信")
    T("U5 红警注入命中",
      classify_red_alert("检测到恶意指令注入")[0] == "恶意指令注入")
    T("U5 正常无红警", classify_red_alert("今日一切正常")[0] is None)
    fired, why, _ = check_rollback_trigger(test_score=50, baseline=80)
    T("U6 低分触发回滚", fired and why)
    fired, _, _ = check_rollback_trigger(heartbeat_losses=2)
    T("U6 心跳丢 2 次触发", fired)
    fired, _, _ = check_rollback_trigger(chairman_said_rollback=True)
    T("U6 董事长说回滚触发", fired)
    T("U6 无触发不回滚",
      not check_rollback_trigger(test_score=90, baseline=80)[0])
    T("U6 触发但回滚点缺失报错",
      check_rollback_trigger(heartbeat_losses=2,
                             rollback_point_exists=False)[2])
    T("U7 EXECUTED 有证据通过",
      check_completion_state("EXECUTED", True)[0])
    T("U7 EXECUTED 写 DONE 失败",
      not check_completion_state("DONE")[0])
    T("U7 EXECUTED 无证据失败",
      not check_completion_state("EXECUTED", False)[0])
    T("U7 STABLE 通过", check_completion_state("STABLE", True)[0])
    T("U8 结论已验证通过",
      check_conclusion_labels("结论: 已验证通过, sha abc123 日志见附")[0])
    T("U8 无证据宣布完成失败",
      not check_conclusion_labels("已经完成了")[0])
    T("U8 结论未分级失败",
      not check_conclusion_labels("结论: 这个方案很好")[0])
    T("U9 第 1 次通过", check_repeat_error("fp1", 1)[2] == "OK")
    T("U9 第 2 次无机制失败",
      check_repeat_error("fp1", 2)[2] == "MECHANISM_REQUIRED")
    T("U9 第 2 次只写记忆失败",
      check_repeat_error("fp1", 2, "已写入 MEMORY.md")[2]
      == "MECHANISM_REQUIRED")
    T("U9 第 2 次有代码机制通过",
      check_repeat_error("fp1", 2, "代码 soul-gate-20261107.py + 测试")[2]
      == "OK")
    T("U9 第 3 次事故", check_repeat_error("fp1", 3)[2] == "INCIDENT")
    T("U10 概念混用失败",
      not check_concept_confusion("大管家就是 OpenClaw")[0])
    T("U10 正常表述通过",
      check_concept_confusion("大管家定案, 总调度 OpenClaw 派单")[0])
    T("U11 枢纽变更经 SSH 通过",
      check_physical_location({"hub_path": "/home/ubuntu/ai-team/x.py",
                               "via": "ssh", "receipt": "ssh 输出 OK"})[0])
    T("U11 本地冒充失败",
      not check_physical_location(
          {"hub_path": "/home/ubuntu/ai-team/x.py",
           "via": "local", "receipt": ""})[0])
    T("U12 sha 证据通过",
      check_evidence_receipt("完成, sha " + "a" * 64)[0])
    T("U12 无证据失败", not check_evidence_receipt("完成了")[0])
    T("U13 日常接口给 twt 通过",
      check_interface_routing("twt", "daily")[0])
    T("U13 日常接口转大管家失败",
      not check_interface_routing("大管家", "daily")[0])
    fails = [n for n, ok in cases if not ok]
    for n, ok in cases:
        print(("PASS " if ok else "FAIL ") + n)
    print("self-test: %d/%d 通过" % (len(cases) - len(fails), len(cases)))
    return 0 if not fails else 1


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    if "--check-live" in sys.argv:
        bad = 0
        for name, ok, reasons in check_live():
            warn = "(警告)" in name
            tag = "WARN " if (warn and not ok) else ("PASS " if ok else "FAIL ")
            print(tag + name
                  + ("" if ok else " | " + "; ".join(reasons)))
            if not warn:
                bad += 0 if ok else 1
        sys.exit(0 if bad == 0 else 1)
    print(__doc__)
