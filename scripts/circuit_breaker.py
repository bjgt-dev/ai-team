#!/usr/bin/env python3
# 第二层·熔断式路由 circuit_breaker.py（2026-10-20）
# 盯 verify.py 裁决（logs/verify-<task_id>.json 的 verdict 字段）：
# 同一任务线（lineage）累计 REJECT >= 2 → 熔断：隔离其待派契约 + 自动生成排障契约 + 写告警。
# PASS 出现则该线计数清零（允许重新熔断）。状态存 state/reject-ledger.json，幂等可重跑。
# 由 cron 每 5 分钟执行；也可手动跑。只读裁决、只动 pending/quarantine/state，不碰 verify.py 本体。
import json, re, shutil, sys
from datetime import datetime
from pathlib import Path

BASE = Path("/home/ubuntu/ai-team")
LOGS, STATE = BASE / "logs", BASE / "state"
PENDING_DIRS = [BASE / "contracts/pending/muse", BASE / "contracts/pending/cue", BASE / "contracts/pending"]
QUAR = BASE / "contracts/quarantine"
LEDGER = STATE / "reject-ledger.json"
THRESHOLD = 2

def lineage(task_id: str) -> str:
    return re.sub(r"(-r\d+|-attempt\d+|-v\d+)$", "", task_id)

def load_ledger():
    if LEDGER.exists():
        return json.loads(LEDGER.read_text())
    return {}

def main():
    ledger = load_ledger()
    verdicts = {}  # lineage -> {"rejects": {file: mtime}, "latest_pass": mtime}
    for f in sorted(LOGS.glob("verify-*.json")):
        try:
            v = json.loads(f.read_text()).get("verdict", "")
        except Exception:
            continue
        task_id = f.name[len("verify-"):-len(".json")]
        lin = lineage(task_id)
        slot = verdicts.setdefault(lin, {"rejects": {}, "latest_pass": 0.0})
        mt = f.stat().st_mtime
        if v == "REJECT":
            slot["rejects"][f.name] = mt
        elif v == "PASS":
            slot["latest_pass"] = max(slot["latest_pass"], mt)

    actions = []
    QUAR.mkdir(parents=True, exist_ok=True)
    for lin, slot in verdicts.items():
        st = ledger.setdefault(lin, {"counted": [], "tripped": False})
        # PASS 在最新 REJECT 之后 → 清零
        if slot["latest_pass"] and slot["rejects"] and slot["latest_pass"] > max(slot["rejects"].values()):
            st["counted"], st["tripped"] = [], False
        new_rejects = [fn for fn in slot["rejects"] if fn not in st["counted"]]
        st["counted"].extend(new_rejects)
        if len(st["counted"]) >= THRESHOLD and not st["tripped"]:
            st["tripped"] = True
            # 1) 隔离待派契约
            moved = []
            for d in PENDING_DIRS:
                if not d.exists():
                    continue
                for c in d.glob("*.json"):
                    try:
                        tid = json.loads(c.read_text()).get("task_id", "")
                    except Exception:
                        continue
                    if lineage(tid) == lin:
                        shutil.move(str(c), str(QUAR / c.name))
                        moved.append(c.name)
            # 2) 自动排障契约（二分法，引用 failure-triage 技能）
            ts = datetime.now().strftime("%Y%m%d%H%M")
            triage_id = f"triage-{lin}-{ts}"
            triage = {
                "task_id": triage_id,
                "task_type": "llm_generate",
                "model": "foreman-prime",
                "objective": (
                    f"任务线 {lin} 已被 verify.py 连续 REJECT {len(st['counted'])} 次，熔断触发。"
                    "请严格按 /home/ubuntu/ai-team/skills/dispatcher/failure-triage/SKILL.md 的二分排错法，"
                    f"阅读裁决文件 {'、'.join('logs/' + fn for fn in st['counted'])} 与对应契约，"
                    "定位失败根因（一次只改一个变量），输出排障报告：根因、证据、二分过程、修复建议、是否建议解隔离重派。"),
                "output_file": "artifacts/triage-report.md",
                "max_tokens": 6000,
                "executor_preference": "muse",
                "acceptance_criteria": [
                    {"type": "file_exists", "target": "artifacts/triage-report.md"},
                    {"type": "file_min_size", "target": "artifacts/triage-report.md", "expected": 200}],
                "evidence_required": ["receipts.jsonl", "manifest.json", "acceptance_self_check.json"],
                "source": "circuit-breaker",
            }
            (BASE / "contracts/pending/muse" / f"{triage_id}.json").write_text(
                json.dumps(triage, ensure_ascii=False, indent=2))
            # 3) 告警
            line = f"{datetime.now().isoformat(timespec='seconds')} CIRCUIT_BREAK lineage={lin} rejects={len(st['counted'])} quarantined={moved} triage={triage_id}"
            with open(STATE / "alerts.log", "a") as fh:
                fh.write(line + "\n")
            (STATE / f"alert-{lin}.md").write_text(f"# 熔断告警\n\n{line}\n")
            actions.append(line)
    LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=2))
    for a in actions:
        print(a)
    print(f"BREAKER_OK lineages={len(verdicts)} tripped={[k for k, v in ledger.items() if v.get('tripped')]}")

if __name__ == "__main__":
    main()
