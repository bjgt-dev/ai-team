#!/usr/bin/env bash
# canary-runner-auditor.sh — E3 (A3): 金丝雀 runner，auditor 身份唯一写入方
#
# 背景：此前 41 行金丝雀由实施方经 postgres 超级用户手工写入 canary_ledger，
# 无独立性；全仓 grep 无任何脚本写 canary_ledger（只有 observe / gate-wiring-audit-db
# 读它），故无“现有 runner”可改归属，本脚本为新建的 auditor 专用 runner。
# 现有其它 canary cron（brain_canary 06:17、pipeline-canary 08:00、timer gateway_canary、
# gate-wiring-audit-db 07:00）均不写 canary_ledger，维持原状。
#
# 用法（以 auditor OS 用户运行，凭据只读 /etc/noesis/db-auditor.env）：
#   canary-runner-auditor.sh --probe    写 1 行探针（gate=auditor-probe），验证 auditor 能写
#   canary-runner-auditor.sh --full     对 gate_registry 18 门禁逐门核验
#                                        （canary 行存在 + last_block_ts 非空），每门写 1 行 auditor 行
#   canary-runner-auditor.sh --dry-run  只打印将写的行，不写库
#   canary-runner-auditor.sh --full --force  绕过 enable 文件（收尾轮次手动重跑用）
# cron（/etc/cron.d/canary-auditor，每日 06:20）调 --full；无 enable 文件时静默跳过、不写库。
# 正式重跑启用（收尾轮次，E2 完成后）：touch /home/ubuntu/ai-team/state/canary-auditor.enabled
#   或直接以 auditor 身份手动跑：sudo -u auditor <本脚本> --full --force
set -euo pipefail

ENV_FILE=/etc/noesis/db-auditor.env
ENABLE_FILE=/home/ubuntu/ai-team/state/canary-auditor.enabled
LOG_FILE=/home/auditor/canary-runner.log
MODE="${1:-}"
FORCE=0
[ "${2:-}" = "--force" ] && FORCE=1

log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG_FILE"; }

psql_aud() {
  # 凭据只从 env 文件读，不打印、不写日志
  set -a; . "$ENV_FILE"; set +a
  PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
    -v ON_ERROR_STOP=1 -q -t -A -F " | " "$@"
}

case "$MODE" in
  --probe)
    psql_aud -c "INSERT INTO public.canary_ledger(seat,gate,expected,actual,ok,provenance) VALUES('auditor','auditor-probe','allow','auditor write probe (E3 verification)','t','auditor');"
    ROW=$(psql_aud -c "SELECT count(*) FROM public.canary_ledger WHERE gate='auditor-probe' AND provenance='auditor';")
    log "PROBE-OK auditor-probe rows=$ROW"
    ;;
  --dry-run)
    echo "--- would insert (one row per registry gate) ---"
    psql_aud -c "SELECT COALESCE(canary_id, gate_id) AS gate, (EXISTS (SELECT 1 FROM public.canary_ledger c WHERE c.gate = g.canary_id)) AS canary_row, (last_block_ts IS NOT NULL) AS has_last_block FROM public.gate_registry g ORDER BY gate_id;"
    echo "--- (no writes in dry-run) ---"
    ;;
  --full)
    if [ "$FORCE" -eq 0 ] && [ ! -f "$ENABLE_FILE" ]; then
      log "FULL-SKIP enable file missing ($ENABLE_FILE); not enabled yet (E2 pending). No rows written."
      exit 0
    fi
    psql_aud -c "INSERT INTO public.canary_ledger (seat, gate, expected, actual, ok, provenance) SELECT 'auditor', COALESCE(canary_id, gate_id), 'wired', 'canary_row=' || CASE WHEN EXISTS (SELECT 1 FROM public.canary_ledger c WHERE c.gate = g.canary_id) THEN 'yes' ELSE 'no' END || ' last_block_ts=' || COALESCE(to_char(last_block_ts,'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), 'null'), (EXISTS (SELECT 1 FROM public.canary_ledger c WHERE c.gate = g.canary_id) AND last_block_ts IS NOT NULL), 'auditor' FROM public.gate_registry g ORDER BY g.gate_id;" >/dev/null
    N=$(psql_aud -c "SELECT count(*) FROM public.canary_ledger WHERE provenance='auditor' AND ts > now() - interval '5 minutes';")
    STATS=$(psql_aud -c "SELECT total || '/' || pass || '/' || fail FROM v_canary_auditor_stats;")
    log "FULL-OK inserted_recent=$N auditor_stats_total/pass/fail=$STATS"
    ;;
  *)
    echo "usage: $0 --probe | --full [--force] | --dry-run" >&2
    exit 2
    ;;
esac
