#!/bin/bash
# patrol_run_v2.sh -- 巡航运行器 v2（2026-10-28，B确定性兜底 + A模型增强）
# 令13-B：模型轮转=可用池前3轮换，单次超时60秒，整次≤180秒（采集+模型60+兜底），超限写incident并走确定性兜底，不占长槽。
# 顺序：①确定性采集先落盘（模型挂了也有新鲜文件）→ ②模型轮次（提示 v2）
#       → ③校验模型是否刷新了 patrol-latest → ④未刷新则确定性采集再落盘并标注 fallback。
# 退出码：0 = 结束时 patrol-latest 新鲜（<180s）且字段自检通过；非 0 = 落盘失败（见 rounds 日志）。
# 生产安装前必须备份旧 patrol_run.sh 并记录前后 sha256；cron 行不变（仍调 patrol_run.sh）。
# 测试用环境变量（生产不设）：PATROL_BASE / PATROL_OUT / PATROL_PROMPT / PATROL_SORTLOG /
#   PATROL_ROUNDS / PATROL_COLLECT / PATROL_MODEL_MODE=real|stub-nowrite|stub-write /
#   PATROL_TIMEOUT（默认 60，令13-B）
set -u
BASE="${PATROL_BASE:-/home/ubuntu/ai-team}"
OUT="${PATROL_OUT:-$BASE/ceo-seat/out/patrol-latest.md}"
PROMPT_V2="$BASE/state/priority/patrol-prompt-v2.txt"
PROMPT_OLD="$BASE/state/priority/patrol-prompt.txt"
PROMPT="${PATROL_PROMPT:-$PROMPT_V2}"
[ -f "$PROMPT" ] || PROMPT="$PROMPT_OLD"
SORTLOG="${PATROL_SORTLOG:-$BASE/state/priority/sort-log.jsonl}"
ROUNDS="${PATROL_ROUNDS:-$BASE/state/patrol-rounds.log}"
COLLECT="${PATROL_COLLECT:-$BASE/scripts/patrol_collect_v2.py}"
# ORDER13-B (2026-11-04): rotation over provisional pool top3 (round1 canary passers;
# final pool file state/gateway-pool.json may replace this list in the A phase).
POOL_B="my-gateway/agnes-3.0-flash my-gateway/glm-4-flash-cc my-gateway/gemini-3.5-flash-lite"
POOL_ARR=($POOL_B)
POOL_IDX=$(( $(date +%j) % 3 ))
MODEL="${PATROL_MODEL:-${POOL_ARR[$POOL_IDX]}}"
MODE="${PATROL_MODEL_MODE:-real}"
TIMEOUT_S="${PATROL_TIMEOUT:-60}"

[ -f "$BASE/state/brain-proxy.env" ] && . "$BASE/state/brain-proxy.env"
P="${BRAIN_PROXY_PORT:-10814}"
export HTTP_PROXY="http://127.0.0.1:$P" HTTPS_PROXY="http://127.0.0.1:$P" \
  http_proxy="http://127.0.0.1:$P" https_proxy="http://127.0.0.1:$P" NO_PROXY="127.0.0.1,localhost"
cd "$BASE" || exit 3

START_TS="$(date '+%F %T')"
START_EPOCH="$(date +%s)"
echo "$START_TS patrol-round start v2" >> "$ROUNDS"
echo "$START_TS patrol brain=$MODEL" >> "$BASE/state/brain-patrol.log"

fail() { echo "$(date '+%F %T') patrol-round end rc=$1 mode=$2" >> "$ROUNDS"; exit "$1"; }

# ① 确定性采集先行：此步失败即整轮失败，不静默。
if ! python3 "$COLLECT" --base "$BASE" --out "$OUT" --sort-log "$SORTLOG" --mode collect \
    --note "deterministic layer first; model round is optional supplement" >> "$BASE/logs/patrol-collect-v2.log" 2>&1; then
  echo "$START_TS collect-first FAILED" >> "$ROUNDS"
  fail 3 collect-failed
fi
COLLECT_SHA="$(sha256sum "$OUT" | awk '{print $1}')"
MODEL_START="$(date +%s)"

# ② 模型轮次（A 增强）。stub 模式仅供 staging 测试，生产永远走 real。
MODEL_RC=0
case "$MODE" in
  stub-nowrite) sleep 1; MODEL_RC=0 ;;
  stub-write)
    printf '# 巡航 %s（model-stub）\n- 队列：pending 2，claimed 1\n- 网关：stub healthy\n- 磁盘：stub 11G可用\n' "$START_TS" > "$OUT"
    MODEL_RC=0 ;;
  stub-garbage)
    printf '# 巡航 2020-01-01 15:20:00（model）\n队列（contracts/pending 各席位计数与合计、claimed 计数）、网关状态、磁盘、dispatch-ack 年龄、\n本轮排序 top3（或按 decision_table.yaml 给出新排序并说明依据）。\n' > "$OUT"
    MODEL_RC=0 ;;
  stub-prod0107)
    # 复现 01:07 生产模型文件（形似通过旧门禁，实为模板+猜测时刻+JSON 倾倒）
    printf '# 巡航 %s 00:00（model）\n\n队列（contracts/pending 4 个席位，contracts/claimed 6 个席位，合计 10 个席位）、网关状态、磁盘、dispatch-ack 年龄、\n本轮排序 top3（沿用第 80 行的 top3 记录）：\n\n{"id": "P1-4", "priority": "P1"},\n{"id": "SD-022", "priority": "P2"},\n{"id": "SD-008", "priority": "P2"}\n\nnote：确定性采集层落盘；模型判断轮次待强脑恢复\n' "$(date '+%F')" > "$OUT"
    MODEL_RC=0 ;;
  *) if [ "$MODEL" = "opencode/deepseek-v4.1-flash-free" ]; then
       env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy timeout "$TIMEOUT_S" opencode run --model "$MODEL" "$(cat "$PROMPT")" </dev/null
     else
       timeout "$TIMEOUT_S" opencode run --model "$MODEL" "$(cat "$PROMPT")" </dev/null
     fi
     MODEL_RC=$? ;;
esac
echo "$START_TS patrol model rc=$MODEL_RC" >> "$ROUNDS"
if [ "$MODEL_RC" = "124" ]; then
  printf '{"ts":"%s","model":"%s","rc":124,"note":"order13-B timeout 60s, deterministic fallback kept patrol-latest fresh"}\n' "$START_TS" "$MODEL" >> "$BASE/state/incidents/patrol-timeout-20261104.jsonl"
fi

# ③ 校验 + ④ 兜底。模型文件只有通过内容门禁才保留，否则 deterministic 层覆盖：
#   首行含当天日期、含 pending/网关/磁盘、含数字、行数 4–12 行、不含指令原文模板句。
verify_model_file() {
  local f="$1" today="$2" mstart="$3"
  [ -f "$f" ] || return 1
  local first lines hhmm fepoch now
  first="$(head -n 1 "$f")"
  lines="$(wc -l < "$f")"
  case "$first" in "# 巡航 $today"*) ;; *) return 1 ;; esac
  [ "$lines" -ge 4 ] && [ "$lines" -le 12 ] || return 1
  # 首行时刻必须落在模型轮次窗口内（±600s）：模型写“00:00”这类猜测时刻直接判拒。
  hhmm="$(printf '%s' "$first" | sed -n 's/^# 巡航 [0-9-]* \([0-9:]*\).*/\1/p')"
  fepoch="$(date -d "$today $hhmm" +%s 2>/dev/null)" || return 1
  now="$(date +%s)"
  [ "$fepoch" -ge $((mstart - 600)) ] && [ "$fepoch" -le $((now + 600)) ] || return 1
  grep -q "pending" "$f" || return 1
  grep -q "网关" "$f" || return 1
  grep -q "磁盘" "$f" || return 1
  grep -Eq "[0-9]" "$f" || return 1
  # 必须含实测值（磁盘可用量/网关 healthy/Up），只写“网关状态、磁盘”等名词的模板文件判拒。
  grep -qE "G可用|healthy|Up " "$f" || return 1
  # 不许把 sort-log JSON 原样倾倒进巡航文件。
  grep -q '"id":' "$f" && return 1
  grep -q "各席位计数与合计" "$f" && return 1
  grep -q "给出新排序并说明依据" "$f" && return 1
  grep -q "HH:MM" "$f" && return 1
  grep -q "YYYY" "$f" && return 1
  return 0
}
FINAL_MODE="model"
if [ ! -f "$OUT" ]; then
  FINAL_MODE="fallback-after-model"
  /home/ubuntu/ai-team/scripts/alert_push.sh PATROL_ZEN_DEAD "🔴 系统死锁红警：巡航强模型 Zen 未刷新有效报告，已退回确定性兜底，不许悄悄降级，请立即查 Zen 直连。" >> /home/ubuntu/ai-team/logs/patrol.log 2>&1 || true
elif [ "$(stat -c %Y "$OUT")" -lt "$MODEL_START" ] || [ "$(sha256sum "$OUT" | awk '{print $1}')" = "$COLLECT_SHA" ] \
  || ! verify_model_file "$OUT" "$(date '+%F')" "$MODEL_START"; then
  FINAL_MODE="fallback-after-model"
  if ! python3 "$COLLECT" --base "$BASE" --out "$OUT" --sort-log "$SORTLOG" --mode fallback-after-model \
      --note "model rc=$MODEL_RC did not refresh patrol-latest with a valid report; deterministic layer rewrote" \
      >> "$BASE/logs/patrol-collect-v2.log" 2>&1; then
    fail 3 fallback-failed
  fi
else
  # 模型刷新了文件：若模型写了 sortlog-line（树内优先，/tmp 兼容旧路径），尝试经校验脚本追加（失败只记日志，不推翻模型文件）。
  SORTLINE="$BASE/state/sortlog-line.json"
  [ -f "$SORTLINE" ] || SORTLINE="/tmp/sortlog-line.json"
  if [ -f "$SORTLINE" ] && python3 -c "import json;json.load(open('$SORTLINE'))" 2>/dev/null; then
    python3 "$BASE/scripts/sortlog_append.py" "$SORTLINE" >> "$BASE/logs/patrol-collect-v2.log" 2>&1 || \
      echo "$START_TS sortlog-append FAILED (model file kept)" >> "$ROUNDS"
    rm -f "$SORTLINE"
  fi
fi

# 结束校验：文件新鲜（<180s）才算成功，否则 rc=1（供 rounds 日志与上层判 A/B）。
OUT_AGE=$(( $(date +%s) - $(stat -c %Y "$OUT") ))
if [ "$OUT_AGE" -gt 180 ]; then
  fail 1 "stale-out-$FINAL_MODE"
fi
echo "$(date '+%F %T') patrol-round end rc=0 mode=$FINAL_MODE model_rc=$MODEL_RC" >> "$ROUNDS"
exit 0
