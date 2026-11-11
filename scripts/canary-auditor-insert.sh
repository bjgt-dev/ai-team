#!/usr/bin/env bash
# canary-auditor-insert.sh — E2 (2026-11-10): auditor 身份写单行金丝雀 + 见证拦截写 last_block_ts。
# 仅以 auditor 身份运行：sudo -u auditor scripts/canary-auditor-insert.sh ...
# 凭据只读 /etc/noesis/db-auditor.env，不打印。last_block_ts 写入仅在
# 调用方已在生产拦截点亲眼看到真实拒绝之后（见证记录），006 授予 auditor
# UPDATE(last_block_ts) 列权限即为此设计；不得无拒绝证据调用 --mark-blocked。
set -euo pipefail
ENV_FILE=/etc/noesis/db-auditor.env
LOG_FILE=/home/auditor/canary-runner.log
GATE=""; EXPECTED=""; ACTUAL=""; OK=""; MARK=""; MARK_ONLY=0
while [ $# -gt 0 ]; do case "$1" in
  --gate) GATE="$2"; shift 2;;
  --expected) EXPECTED="$2"; shift 2;;
  --actual) ACTUAL="$2"; shift 2;;
  --ok) OK="$2"; shift 2;;
  --mark-blocked) MARK="$2"; shift 2;;
  --mark-only) MARK_ONLY=1; shift;;
  *) echo "unknown arg: $1" >&2; exit 2;;
esac; done
if [ "$MARK_ONLY" -eq 1 ]; then
  [ -n "$MARK" ] || { echo "usage: $0 --mark-only --mark-blocked GATE_ID" >&2; exit 2; }
else
if [ -z "$GATE" ] || [ -z "$EXPECTED" ] || [ -z "$ACTUAL" ] || { [ "$OK" != "true" ] && [ "$OK" != "false" ]; }; then
  echo "usage: $0 --gate G --expected E --actual A --ok true|false [--mark-blocked GATE_ID]" >&2; exit 2
fi
fi
esc() { printf "%s" "$1" | sed "s/'/''/g"; }
set -a; . "$ENV_FILE"; set +a
export PGPASSWORD="${PGPASSWORD:-}"  # supplied via ENV_FILE; never hardcode a password here
PSQL="psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -v ON_ERROR_STOP=1 -q -t -A"
if [ "$MARK_ONLY" -eq 0 ]; then
$PSQL -c "INSERT INTO public.canary_ledger(seat,gate,expected,actual,ok,provenance) VALUES('auditor','$(esc "$GATE")','$(esc "$EXPECTED")','$(esc "$ACTUAL")','$OK','auditor');"
fi
if [ -n "$MARK" ]; then
  # NOTE: auditor holds UPDATE(last_block_ts) ONLY (006); do NOT touch updated_at.
  $PSQL -c "UPDATE public.gate_registry SET last_block_ts=now() WHERE gate_id='$(esc "$MARK")';"
  TS=$($PSQL -c "SELECT to_char(last_block_ts,'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') FROM public.gate_registry WHERE gate_id='$(esc "$MARK")';")
  if [ "$MARK_ONLY" -eq 1 ]; then
    echo "[$(date -u +%FT%TZ)] E2-MARK-ONLY mark_blocked=$MARK last_block_ts=$TS" | tee -a "$LOG_FILE"
  else
    echo "[$(date -u +%FT%TZ)] E2-INSERT gate=$GATE ok=$OK mark_blocked=$MARK last_block_ts=$TS" | tee -a "$LOG_FILE"
  fi
else
  echo "[$(date -u +%FT%TZ)] E2-INSERT gate=$GATE ok=$OK" | tee -a "$LOG_FILE"
fi
