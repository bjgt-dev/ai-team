#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# run-incremental.sh —— 情报增量扫 Harness：跑一轮生产增量扫（Phase 1）。
# 只做编排与取证，不改生产脚本：采集走枢纽 daily_intel_pipeline.sh
# --incremental（带过载保护与 cron_flock 锁），本脚本只负责跑之前/
# 跑之后的状态取证，写进 runs/<RUN_ID>/。
# 用法：bash scripts/run-incremental.sh
# 产物：runs/<RUN_ID>/{result.json,collect.err,meta.json,record-tail.txt}
# 退出码：0 = 采集命令跑完（结果好坏由 check-run.py 判定）；
#         非 0 = SSH 或取证本身失败。
# ─────────────────────────────────────────────────────────────
set -uo pipefail
HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$HARNESS_DIR/config.env"

ssh_hub() { ssh -o ConnectTimeout=30 "$HUB_HOST" "$1"; }

RUN_ID="$(ssh_hub 'date +%Y%m%d-%H%M' | tr -d '[:space:]')"
DATE="$(ssh_hub 'date +%F' | tr -d '[:space:]')"
RUN_DIR="$HARNESS_DIR/runs/$RUN_ID"
mkdir -p "$RUN_DIR"
printf 'RUN_ID=%s\nDATE=%s\n' "$RUN_ID" "$DATE" | tee "$RUN_DIR/run-id.txt"

# ── 跑前取证 ──
ssh_hub "grep -c '' $HUB_BASE/$REGISTRY_FILE; awk '/^## 七、待验技能线索/{f=1;next} f' $HUB_BASE/$REGISTRY_FILE | grep -c ''; wc -l < $HUB_BASE/$STATE_DIR/intel-incremental-$DATE.md 2>/dev/null || echo 0; ls $HUB_BASE/$STATE_DIR/outbox/intel-new-free-$DATE-*.md 2>/dev/null | tr '\n' ' '; echo" \
  > "$RUN_DIR/before.txt" 2> "$RUN_DIR/ssh-before.err" || echo "WARN: before-state failed (see ssh-before.err)"
# before.txt 四行：登记表总行数 / 第七节行数 / 当日增量记录行数 / 当日报警文件列表

# ── Phase 1：采集（生产脚本原样跑，带锁；过载时它自己走 --skip-collect）──
# 生产脚本把 "source X failed" 这类行也打到 stdout，result.json 只留 JSON 行，
# 全量输出另存 result-full.txt 备查。
ssh_hub "$HUB_BASE/$FLOCK $FLOCK_NAME $HUB_BASE/$PIPELINE_SH --incremental" \
  > "$RUN_DIR/result-full.txt" 2> "$RUN_DIR/collect.err"
RC=$?
grep -E '^\{' "$RUN_DIR/result-full.txt" | tail -1 > "$RUN_DIR/result.json" || true
echo "collect_rc=$RC" >> "$RUN_DIR/run-id.txt"

# ── 跑后取证 ──
ssh_hub "grep -c '' $HUB_BASE/$REGISTRY_FILE; awk '/^## 七、待验技能线索/{f=1;next} f' $HUB_BASE/$REGISTRY_FILE | grep -c ''; wc -l < $HUB_BASE/$STATE_DIR/intel-incremental-$DATE.md 2>/dev/null || echo 0; ls $HUB_BASE/$STATE_DIR/outbox/intel-new-free-$DATE-*.md 2>/dev/null | tr '\n' ' '; echo; tail -30 $HUB_BASE/$STATE_DIR/intel-incremental-$DATE.md 2>/dev/null" \
  > "$RUN_DIR/after.txt" 2> "$RUN_DIR/ssh-after.err" || echo "WARN: after-state failed (see ssh-after.err)"
# after.txt：前三行同 before；第四行当日报警文件列表；第五行起为增量记录末 30 行
tail -n +5 "$RUN_DIR/after.txt" > "$RUN_DIR/record-tail.txt"

python3 - "$RUN_DIR" <<'PYEOF'
import json, sys
d = sys.argv[1]
def lines(p):
    try:
        with open(p, encoding="utf-8") as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return []
before = lines(f"{d}/before.txt")
after = lines(f"{d}/after.txt")
def ints(ls):
    out = []
    for x in ls[:3]:
        try:
            out.append(int(x.strip()))
        except ValueError:
            out.append(-1)
    return out
b, a = ints(before), ints(after)
alerts_before = before[3].split() if len(before) > 3 else []
alerts = after[3].split() if len(after) > 3 else []
meta = {
    "run_id": open(f"{d}/run-id.txt").read().splitlines()[0].split("=", 1)[1],
    "date": open(f"{d}/run-id.txt").read().splitlines()[1].split("=", 1)[1],
    "registry_lines_before": b[0], "registry_lines_after": a[0],
    "registry_vii_before": b[1], "registry_vii_after": a[1],
    "record_lines_before": b[2], "record_lines_after": a[2],
    "alert_files_before": alerts_before,
    "alert_files_after": alerts,
}
with open(f"{d}/meta.json", "w", encoding="utf-8") as f:
    json.dump(meta, f, ensure_ascii=False, indent=1)
print(json.dumps(meta, ensure_ascii=False))
PYEOF
echo "run dir: $RUN_DIR"
