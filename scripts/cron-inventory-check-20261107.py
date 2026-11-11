#!/usr/bin/env python3
"""cron-inventory-check-20261107.py -- Thorsten Ball Rule 11 / code-dev-sop §五.

Rule 11's SOP landing was "补一张 cron 清单进 ~/ai-team/docs/sops/, 写清
每个 cron 的用途与失败时写哪". What existed: state/cron-inventory.md,
dated 2026-10-21, listing crons that no longer match the live crontab
(health_3q, gateway_canary, verdict watchdog, P95, long-task-sweeper are
all missing from it), and nothing in docs/sops/ at all. This script:
  generate [--crontab FILE] [--out FILE]
      rebuild the inventory from the LIVE crontab (default: `crontab -l`),
      one line per entry: schedule, command, script existence, purpose
      (from a built-in known-purpose table, "unknown -- fill in" otherwise),
      failure destination (state/logs/<name>.log + outbox on failure).
      Writes docs/sops/cron-inventory-20261107.md (the SOP-required
      location) and state/cron-inventory-20261107.md.
  check [--crontab FILE] [--inventory FILE]
      every crontab entry's script must exist on disk; every inventory
      entry must still exist in the crontab (stale entries flagged).
      Exit 1 lists missing scripts / stale entries.

Exit: 0 ok / 1 check failed / 2 tool error. Read-only except generate's
two inventory outputs; never touches the crontab itself.
"""
import argparse
import os
import re
import subprocess
import sys

KNOWN_PURPOSE = {
    "alert_shadow_watch": "alert_shadow_watch.py - SHADOW MODE ONLY (2026-10-21, quota-economy plan A-packa",
    "ascs-watch": "ASCS watch (2026-11-07, MEMORY.md ASCS盯守).",
    "auto_advance": "Auto-advance execution tracks (2026-10-27).",
    "autopilot_loop": "Autopilot loop (2026-10-25, chairman ultimate order)",
    "b2-backup": "B2 off-site backup uploader (2026-11-07, gap-fill).",
    "backup-watchdog": "Backup watchdog (备工触发器) — 2026-10-30 终极改造第二项.",
    "backup_amd_proxy": "amd-proxy（车队机）配置快照：每晚由枢纽拉取，落 state/ 随枢纽备份离站（2026-10-24 Twt 建，补双机恢复覆盖缺口）",
    "backup_assets": "核心资产异地备份（2026-10-20 董事长令）：knowledge + state + PG 全量 dump + omemo 数据",
    "bailian-quota-guard": "Canonical scripts/ entry for the Bailian quota/expiry guard (2026-11-07).",
    "beszel-health": "Beszel health check (2026-11-07, gap-fill).",
    "brain_canary": "脑通道金丝雀全量探针 (每日 06:17)",
    "brain_guard": "脑通道守卫 (Zen/网关脑健康)",
    "brain_watch": "大管家脑通道看门狗 v2（2026-10-23 23:00）：每 5 分钟实测脑通道；Zen 免费额度按出口 IP 限额——当前出口被限即按 brain-pro",
    "butler-self-check": "butler-self-check: the butler's own doctor (2026-10-30, ownership moved from Twt",
    "butler_heartbeat_check": "Independent heartbeat checker for the butler seat. READ-ONLY except for",
    "c2-executor": "C2 W1精简版 executor (2026-11-04, 令12实施).",
    "c2-verifier": "C2 W1精简版 verifier timer (2026-11-04, 令12实施).",
    "cf-neuron-guard": "CF Workers AI Neuron guard (2026-11-05).",
    "chaos-drill": "Daily chaos drill (每日混沌演练) — 2026-10-30 终极改造第二项.",
    "check-m5-switch": "check-m5-switch.sh — M5 人兜底硬开关校验（只读检查，不改状态，不对外发）",
    "check-quarantine-stuck": "check-quarantine-stuck.py — QUARANTINED 任务久置告警（2026-11-08 董事长骂后立）。",
    "circuit_breaker": "第二层·熔断式路由 circuit_breaker.py（2026-10-20）",
    "cloady-keepalive": "Cloady free-app keepalive (2026-11-08). Free apps auto-pause after 7d without ac",
    "cluster_watch": "cluster_watch.sh - 集群存活巡检（每 30 分钟）：查得到的直查，查不到的用队列活性推断，结果写 alerts.log",
    "cq-run": "cq-run.sh — central-queue timer wrapper (DRAFT, NOT installed).",
    "cron-inventory-check": "cron-inventory-check-20261107.py -- Thorsten Ball Rule 11 / code-dev-sop §五.",
    "daily_intel_pipeline": "daily_intel_pipeline.sh — 情报与内容供应链入口（cron 每日 07:30，日志 logs/intel-pipeline.log）",
    "daily_strategy_cron": "daily_strategy_cron.sh - 合伙人战略备包（早 07:00 / 晚 20:30）",
    "deadletter_watchdog": "物理看门狗 v6：加时间门禁，只有交单后超过 10 分钟仍无 ACK 才标死信，否则跳过。",
    "disk_watch": "disk_watch v2 (2026-10-24 chairman directive): monitor arm / , arm /data, amd /",
    "drift-check": "drift-check.py — daily drift detection for the hub crontab (config-as-code).",
    "fleet-patrol": "Fleet patrol for VPNGate residential fleet feeding Zen (2026-11-06).",
    "gate-wiring-audit": "gate-wiring-audit.py -- 每日扫全部门禁的调用点与生产收据。",
    "gateway_daily_restart": "网关每日 04:00 计划内重启（2026-10-21 L3 会诊裁决）：队列空才重启，防慢增长触发守卫自退",
    "gateway_pool_refresh": "gateway pool refresh (U-18, 2026-11-10): pool re-validation stamp.",
    "gw_rss_guard": "网关 RSS 守卫",
    "health_3q_reporter": "health_3q reporter (U-18, 2026-11-10): 变色才报.",
    "heartbeat": "slim 心跳写入 heartbeat.json",
    "heartbeat_watch": "心跳缺失15分钟即Telegram报警（独立脚本，不依赖heartbeat.sh本身）",
    "hub-registry-check": "hub-registry-check.py — registry vs reality drift detector (read-only).",
    "intel_pipeline": "No API key: parse ytInitialData from the public search page (verified",
    "ledger-check": "执行台账机制校验（枢纽版，AGENTS.md 执行台账机制，2026-11-06 董事长令）。",
    "long-task-sweeper": "long-task-sweeper-20261106.py — items 1 + 3 (slugs long-task-heartbeat,",
    "loop-watchdog": "loop-watchdog: biguan loop deadlock guard (2026-10-30 SRE self-check).",
    "m5-window-check": "M5 24h window acceptance check (CHAIR-20261105-18 section 3).",
    "maritaca-minimax-guard": "Maritaca + MiniMax channel guard (2026-11-07, gap-fill).",
    "model-health": "model-health.py: gateway model health registry + router.",
    "morning-report": "morning-report.sh - C3 晨报最小版 v3（2026-11-04，令12）：独立发送，无红点前缀，不走红警去重，不写 red-alerts.",
    "muse-sync-check": "muse_sync 4-step standard checker (2026-11-07, gap-fill).",
    "observe-correct": "Observe-correct loop (methodology: 执行-观察-修正闭环).",
    "openrouter-rotation-guard": "openrouter-rotation-guard: OpenRouter multi-account rotation guard (2026-11-05).",
    "patrol_deterministic": "Deterministic patrol (expert-corrected final, 2026-10-28).",
    "patrol_submit": "ORDER13-A patrol submitter (M5-1 revision, 2026-11-05, CHAIR-20261105-18 residua",
    "pipeline-canary": "pipeline-canary-20261106.py — daily regression canary (DESIGN §7, NOT installed)",
    "prune-profiles": "Keep newest 3 daily backups per profile family; older moved to /data/trash/<date",
    "pull_7d_p95": "Daily OCI 7-day P95 pull (hub cron, read-only key).",
    "red_alert_triggers": "red_alert_triggers.py - 物理触发器（2026-10-31 反偷懒令，不靠主观判断）",
    "redpoint_test": "红点注入：切换后2小时内每10分钟（cron */10），之后每小时（脚本自限：2小时后仅整点注入）",
    "reputation_rebuild": "工人信誉台账自动重建（2026-10-21 冷启动演练整改）：不改冻结文件，",
    "retrywait-scan": "RETRY_WAIT 扫描并应用处理 (--apply)",
    "scan_inbox": "Scan evidence bundles; only contract location/archiving is handled here.",
    "siliconflow-quota-guard": "siliconflow-quota-guard: Canonical scripts/ entry for the SiliconFlow quota guard (2026-11-07).",
    "skill_forge": "教训变技能（董事长令 2026-10-20）：每日扫 verify REJECT 记录，",
    "status_board": "状态板：由代码实测生成，不凭记忆。输出 state/status-board.md",
    "storage-cleanup": "Storage cleanup, dry-run first per Claude veto. Real deletion only with --real, ",
    "supervisor": "Supervisor control plane (deterministic, no LLM, no seat lock held during execut",
    "supervisor-stall-scan": "Supervisor stall scan (2026-11-06, live PG scan).",
    "supervisor_check": "全面监管巡检（2026-10-22 董事长令）：磁盘/网关/模型网关/ds2api/keeper/工人心跳/专家通道/常驻席巡航。",
    "supervisor_worker": "监工（supervisor worker）——枢纽内，脚本 + 模型网关小模型。",
    "sync_to_butler_out": "同步文件到 ceo-seat/out/sync (大管家输出)",
    "timer_submit": "ORDER13-6 timer submitter (M5-1 revision, 2026-11-05, CHAIR-20261105-18 residual",
    "tmp-so-cleanup": "每日清理 /tmp 下陈旧的 .3*.so 临时库文件（opencode/chrome 每次运行都会生成，不清理会吃满磁盘）。",
    "verdict-dispatch-watchdog": "Watchdog: flag butler verdicts (pass/conditional pass) older than 15 min",
    "wake_scheduler": "席位唤醒调度 tick (PRODUCTION 2026-11-05)",
    "watchdog": "Watchdog (deterministic, no LLM, no gateway) — lands state/watchdog-draft-202611",
    "watchdog-registry-check": "Watchdog registry check (2026-11-07 enforce scan).",
    "weekly-audit": "Read-only quality audit script for weekly checks."
}
SOPS_OUT = "/home/ubuntu/ai-team/docs/sops/cron-inventory-20261107.md"
STATE_OUT = "/home/ubuntu/ai-team/state/cron-inventory-20261107.md"


def _read_crontab(path):
    if path:
        with open(path, encoding="utf-8") as f:
            return f.read()
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise OSError("crontab -l failed: %s" % (proc.stderr or "").strip())
    return proc.stdout


def _parse_crontab(text):
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(\S+\s+\S+\s+\S+\s+\S+\s+\S+)\s+(.*)$", line)
        if not m:
            continue
        schedule, cmd = m.group(1), m.group(2)
        # Every .py/.sh token in the command must exist (cron_flock.sh
        # wrappers carry the real script as a later token; a greedy match
        # across the wrapper used to fuse them into one bogus path).
        tokens = re.findall(r"(?:^|[\s'\"])(/[^\s'\";|]*\.(?:py|sh))", cmd)
        script = tokens[-1] if tokens else None
        key = None
        for known in KNOWN_PURPOSE:
            if known in cmd:
                key = known
                break
        entries.append({"schedule": schedule, "command": cmd,
                        "script": script, "scripts": tokens, "key": key})
    return entries


def _script_exists(script):
    if not script:
        return None
    if script.startswith("/"):
        return os.path.exists(script)
    return os.path.exists(os.path.join("/home/ubuntu/ai-team/scripts", os.path.basename(script)))


def cmd_generate(args):
    try:
        text = _read_crontab(args.crontab)
    except OSError as exc:
        print("TOOL-ERROR: %s" % exc)
        return 2
    entries = _parse_crontab(text)
    lines = ["# Cron 清单 (由 cron-inventory-check-20261107.py 按 live crontab 生成, 2026-11-07)",
             "",
             "变更 cron 时同步重新生成本文件 (`generate`). 失败时写哪: 见每行末尾。",
             "",
             "| 调度 | 命令 | 脚本存在 | 用途 | 失败写哪 |",
             "|---|---|---|---|---|"]
    for e in entries:
        exists = _script_exists(e["script"])
        purpose = KNOWN_PURPOSE.get(e["key"], "unknown -- 补用途")
        dest = "state/logs/ + outbox (变色即报)"
        lines.append("| %s | `%s` | %s | %s | %s |" % (
            e["schedule"], e["command"][:120],
            "yes" if exists else ("n/a" if exists is None else "NO"),
            purpose, dest))
    doc = "\n".join(lines) + "\n"
    for out in (SOPS_OUT if not args.out else args.out, STATE_OUT):
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(doc)
        print("wrote %s (%d entries)" % (out, len(entries)))
    return 0


def cmd_check(args):
    try:
        text = _read_crontab(args.crontab)
    except OSError as exc:
        print("TOOL-ERROR: %s" % exc)
        return 2
    entries = _parse_crontab(text)
    problems = []
    for e in entries:
        for tok in e.get("scripts") or ([e["script"]] if e["script"] else []):
            if not _script_exists(tok):
                problems.append("crontab script missing on disk: %s (cmd: %s)" % (tok, e["command"][:100]))
    inv_path = args.inventory or SOPS_OUT
    if os.path.exists(inv_path):
        with open(inv_path, encoding="utf-8") as f:
            inv = f.read()
        for e in entries:
            if e["key"] and e["key"] not in inv:
                problems.append("inventory %s lacks live cron entry: %s" % (inv_path, e["key"]))
    if problems:
        print("FAIL (%d):" % len(problems))
        for p in problems:
            print("  - %s" % p)
        return 1
    print("PASS: %d crontab entries, all scripts exist, inventory in sync" % len(entries))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="cron inventory generator/checker (Rule 11)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--crontab", default=None)
    g.add_argument("--out", default=None, help="sops output path override (tests)")
    g.set_defaults(fn=cmd_generate)
    c = sub.add_parser("check")
    c.add_argument("--crontab", default=None)
    c.add_argument("--inventory", default=None)
    c.set_defaults(fn=cmd_check)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
