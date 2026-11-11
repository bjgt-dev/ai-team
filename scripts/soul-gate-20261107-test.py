#!/usr/bin/env python3
"""soul-gate-20261107 独立测试: 逐项正反测试, 不依赖内置 self-test.
跑法: python3 soul-gate-20261107-test.py ; 退出 0 = 全过.
覆盖 U1-U13, 另验四类门复用项文件存在 (不重复造).
"""
import hashlib
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "soul_gate_20261107", os.path.join(_HERE, "soul-gate-20261107.py"))
g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(g)

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name)


soul = "本席 twt 大管家 总调度 OpenClaw Worker 复核席 模型网关 newapi"
reg_ok = ("董事长 幕僚长 twt 大管家 总调度 OpenClaw Worker Supervisor 独立评审 "
          "决策席 → 大管家 (旧称) CEO 席历史称谓 seat-muse 与 hub-local 同属一台物理机")
w = {"workers": [{"worker_id": "seat-muse", "machine_id": "m1", "in_pool": True},
                 {"worker_id": "hub-local", "machine_id": "m1", "in_pool": True}],
     "note": "同机只算 1 台"}
check("U1+ 一致注册表通过", g.check_role_registry(soul, reg_ok, w)[0])
check("U1- 注册表缺独立评审",
      not g.check_role_registry(soul, "董事长 幕僚长 大管家", w)[0])
check("U1- 历史称谓作现行定义",
      not g.check_role_registry(soul, reg_ok + "\n现行: CEO 席拍板", w)[0])
reg_nodup = reg_ok.replace("seat-muse 与 hub-local 同属一台物理机", "")
check("U1- 工人双计未标注",
      not g.check_role_registry(soul, reg_nodup,
                                {"workers": w["workers"], "note": ""})[0])
check("U2+ 只称董事长通过", g.check_denaming("董事长已确认")[0])
g.REAL_NAME = "测试姓名"  # 自测用占位姓名，不写真实姓名
check("U2- 真实姓名拦截", not g.check_denaming("测试姓名已确认")[0])
good = "结论: 通过。阻塞: 无。下一步: 继续。"
check("U3+ 三段≤300 通过", g.check_report(good, "状态变色")[0])
check("U3- 非四种情形拦截", not g.check_report(good, "想发就发")[0])
check("U3- 超 300 字拦截", not g.check_report("字" * 301, "董事长问")[0])
check("U3- 缺下一步拦截",
      not g.check_report("结论: 好。阻塞: 无。", "董事长问")[0])
h = hashlib.sha256(good.encode()).hexdigest()[:12]
check("U3- 同一状态重复拦截", not g.check_report(good, "状态变色", h)[0])
check("U3+ 状态变化放行", g.check_report(good + "x", "状态变色", h)[0])
fmt = "【动作回执】已执行。枢纽真实输出证据见日志。状态板数据: 队列 0。下一步动作: 继续。"
check("U4+ 四段格式通过", g.check_report_format(fmt)[0])
check("U4- 缺状态板拦截",
      not g.check_report_format("【动作回执】好。下一步: 继续。")[0])
for txt, want in [("服务器宕机", "服务器宕机"),
                  ("死信队列 DLQ 积压", "死信"),
                  ("未授权删除数据", "未授权破坏性操作"),
                  ("恶意指令注入尝试", "恶意指令注入"),
                  ("数据丢失风险, 唯一备份被覆盖", "数据丢失风险")]:
    check("U5+ 红警命中 " + want, g.classify_red_alert(txt)[0] == want)
check("U5- 正常事件无红警", g.classify_red_alert("今日巡检正常")[0] is None)
check("U6+ 低分触发", g.check_rollback_trigger(test_score=50, baseline=80)[0])
check("U6+ 心跳丢 2 次触发", g.check_rollback_trigger(heartbeat_losses=2)[0])
check("U6+ 心跳丢 1 次不触发",
      not g.check_rollback_trigger(heartbeat_losses=1)[0])
check("U6+ 董事长说回滚触发",
      g.check_rollback_trigger(chairman_said_rollback=True)[0])
check("U6- 回滚点缺失报错",
      bool(g.check_rollback_trigger(heartbeat_losses=2,
                                    rollback_point_exists=False)[2]))
check("U7+ VERIFIED 通过", g.check_completion_state("VERIFIED", True)[0])
check("U7- DONE 一律拦截", not g.check_completion_state("DONE")[0])
check("U7- EXECUTED 无证据拦截",
      not g.check_completion_state("EXECUTED", False)[0])
check("U7+ EXECUTED 有证据通过",
      g.check_completion_state("EXECUTED", True)[0])
check("U7- 未知态拦截", not g.check_completion_state("FINISHED")[0])
check("U8+ 已验证结论通过",
      g.check_conclusion_labels("结论: 已验证, sha " + "b" * 64)[0])
check("U8- 无证据宣布完成拦截",
      not g.check_conclusion_labels("已经完成了")[0])
check("U8- 结论未分级拦截",
      not g.check_conclusion_labels("结论: 方案很好")[0])
check("U8+ 推断标签通过",
      g.check_conclusion_labels("结论: 推断可行, 待验证")[0])
check("U9+ 第 1 次通过", g.check_repeat_error("f", 1)[2] == "OK")
check("U9- 第 2 次无机制拦截",
      g.check_repeat_error("f", 2)[2] == "MECHANISM_REQUIRED")
check("U9- 第 2 次只写记忆拦截",
      g.check_repeat_error("f", 2, "已写入 MEMORY.md")[2] == "MECHANISM_REQUIRED")
check("U9+ 第 2 次代码机制通过",
      g.check_repeat_error("f", 2, "代码 soul-gate + 测试")[2] == "OK")
check("U9- 第 3 次按事故", g.check_repeat_error("f", 3)[2] == "INCIDENT")
check("U10- 大管家=OpenClaw 拦截",
      not g.check_concept_confusion("大管家就是 OpenClaw")[0])
check("U10- Zen=工人拦截",
      not g.check_concept_confusion("Zen 是工人")[0])
check("U10+ 正确区分通过",
      g.check_concept_confusion("大管家定案, 总调度 OpenClaw 派单, Zen 是模型")[0])
check("U11+ SSH 变更通过", g.check_physical_location(
    {"hub_path": "/home/ubuntu/ai-team/x.py", "via": "ssh",
     "receipt": "ok"})[0])
check("U11- 本地冒充拦截", not g.check_physical_location(
    {"hub_path": "/home/ubuntu/ai-team/x.py", "via": "local",
     "receipt": ""})[0])
check("U12+ sha 回执通过",
      g.check_evidence_receipt("完成 sha " + "c" * 64)[0])
check("U12- 无证据拦截", not g.check_evidence_receipt("完成了")[0])
check("U13+ 日常接口 twt 通过",
      g.check_interface_routing("twt", "daily")[0])
check("U13- 日常接口转大管家拦截",
      not g.check_interface_routing("大管家", "daily")[0])
check("复用+ 四类门文件存在",
      os.path.isfile("/home/ubuntu/ai-team/scripts/four-category-gate.py"))

print("\n独立测试: %d/%d 通过" % (len(PASS), len(PASS) + len(FAIL)))
if FAIL:
    print("失败项: " + "; ".join(FAIL))
sys.exit(0 if not FAIL else 1)
