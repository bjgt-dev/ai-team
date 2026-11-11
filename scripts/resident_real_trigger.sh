#!/bin/bash
TRIGGER=$(date +%s)
echo "trigger_epoch=$TRIGGER trigger_iso=$(date '+%Y-%m-%d %H:%M:%S')" | tee /home/ubuntu/ai-team/state/butler-seat-revive/resident-trigger-20261028.log
/home/ubuntu/ai-team/scripts/ceo-opencode.sh "救活复核：恢复巡航，刷新 ceo-seat/out/patrol-latest.md，回执须含工单号、状态、回执四字段（output_sha256 须为真实 64 位十六进制、created_at 与文件时间一致）、证据路径、验收结论；巡航未刷新不得声称已刷新。"
RC=$?
echo "runner_rc=$RC" | tee -a /home/ubuntu/ai-team/state/butler-seat-revive/resident-trigger-20261028.log
python3 - "$TRIGGER" <<'PYEOF'
import sys, time, json, importlib.util
trigger = float(sys.argv[1])
spec = importlib.util.spec_from_file_location("bsr", "/home/ubuntu/ai-team/scripts/butler_seat_revive.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
now = time.time()
patrol = "/home/ubuntu/ai-team/ceo-seat/out/patrol-latest.md"
ack = "/home/ubuntu/ai-team/state/dispatch-ack.log"
p_age = m.age(patrol, now)
valid, fake, cand = m.check_receipt("/home/ubuntu/ai-team/ceo-seat/out", trigger, p_age)
rep = {
    "trigger_epoch": trigger,
    "runner_done": True,
    "ack_new": m.ack_line_after_trigger(ack, trigger),
    "patrol_after_trigger": bool(m.mtime(patrol) and m.mtime(patrol) >= trigger - 5),
    "patrol_age_after_s": p_age,
    "receipt_valid": valid,
    "receipt_fake_reasons": fake,
    "receipt_candidate": cand,
}
rep["triple"] = bool(rep["ack_new"] and rep["patrol_after_trigger"] and p_age is not None and p_age <= 300 and valid)
with open("/home/ubuntu/ai-team/state/butler-seat-revive/resident-trigger-report-20261028.json", "w") as f:
    json.dump(rep, f, ensure_ascii=False, indent=2)
print(json.dumps(rep, ensure_ascii=False, indent=2))
PYEOF
