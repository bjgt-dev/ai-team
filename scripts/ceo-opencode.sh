#!/bin/bash
# 枢纽大管家席（历史文件名 ceo-opencode.sh 保留，角色已按 2026-11-02 审计注册表更名）（OpenCode 载体）运行器：用法 ceo-opencode.sh "任务文本" [--model x]
# 脑通道：默认 opencode/deepseek-v4.1-flash-free（免费强模型）；兜底 my-gateway/deepseek-chat。
set -u
export MY_GATEWAY_API_KEY="YQw3UhOdOusdYPdd2vrytEMWwCqtQoIOtscLQuLLyCd0CjOG"
[ -f /home/ubuntu/ai-team/state/brain-proxy.env ] && . /home/ubuntu/ai-team/state/brain-proxy.env
P=${BRAIN_PROXY_PORT:-10814}
export HTTP_PROXY=http://127.0.0.1:$P HTTPS_PROXY=http://127.0.0.1:$P http_proxy=http://127.0.0.1:$P https_proxy=http://127.0.0.1:$P NO_PROXY=127.0.0.1,localhost,127.0.0.1 no_proxy=127.0.0.1,localhost,127.0.0.1
cd /home/ubuntu/ai-team/ceo-seat
# --- 2026-11-02 shortlock: 短锁领单 + 独立执行 + 短锁回写（第一批定案） ---
# 路径可经环境变量覆盖，仅供沙箱测试；生产默认值与旧架构一致。
LOCK_FILE="${CEO_LOCK_FILE:-/tmp/ceo-opencode.lock}"
HOLDER_FILE="${CEO_HOLDER_FILE:-/tmp/ceo-opencode.holder}"
CLAIM_DIR="${CEO_CLAIM_DIR:-/tmp}"
FLAG_FILE="${CEO_FLAG_FILE:-/home/ubuntu/ai-team/state/priority-external-task}"
SEAT_LOG="${CEO_SEAT_LOG:-/home/ubuntu/ai-team/logs/ceo-seat.log}"
DEADLETTER_DIR="${CEO_DEADLETTER_DIR:-/home/ubuntu/ai-team/state/butler-seat-revive}"

proc_start_epoch() {
  # pid -> 启动 epoch 秒（经 ps etimes 换算）；进程不存在则失败。
  local pid="$1" etimes
  [ -d "/proc/$pid" ] || return 1
  etimes="$(ps -o etimes= -p "$pid" 2>/dev/null | tr -d ' ')"
  [ -n "$etimes" ] || return 1
  echo "$(( $(date +%s) - etimes ))"
}

claim_active() {
  # claim 文件对应的会话是否经校验仍活跃：pid 存活且 start_epoch 与
  # now - etimes 偏差 <= 10s；否则视为过期或 PID 重用，返回失败。
  local cf="$1" cpid cstart pest diff
  cpid="$(sed -n 's/^pid=//p' "$cf" 2>/dev/null | head -1)"
  cstart="$(sed -n 's/^start_epoch=//p' "$cf" 2>/dev/null | head -1)"
  [ -n "$cpid" ] && [ -n "$cstart" ] || return 1
  pest="$(proc_start_epoch "$cpid")" || return 1
  diff=$(( pest - cstart ))
  [ "${diff#-}" -le 10 ]
}

phase1_claim() {
  # 阶段1 短锁领单（持锁 < 2s）：扫过期 claim、判活跃会话、写本会话 claim+holder。
  # 成功返回 0；拒绝返回 1（不杀旧会话，不改旧会话任何文件）。
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    echo "$(date '+%F %T') [SKIP] ceo-opencode reason=seat_lock_busy previous_run_still_holding_lock" >> "$SEAT_LOG"
    echo "[SKIP] ceo-opencode reason=seat_lock_busy" >&2
    exec 9>&-
    return 1
  fi
  local now active_claim="" active_pid="" active_start=0 cf
  now="$(date +%s)"
  for cf in "$CLAIM_DIR"/ceo-opencode.claim.*; do
    [ -e "$cf" ] || continue
    if claim_active "$cf"; then
      active_claim="$cf"
      active_pid="$(sed -n 's/^pid=//p' "$cf" | head -1)"
      active_start="$(sed -n 's/^start_epoch=//p' "$cf" | head -1)"
    else
      rm -f "$cf"
    fi
  done
  if [ -n "$active_claim" ]; then
    local session_age flag_age
    session_age=$(( now - active_start ))
    if [ -f "$FLAG_FILE" ]; then
      flag_age=$(( now - $(stat -c %Y "$FLAG_FILE") ))
      if [ "$session_age" -gt 1800 ] && [ "$flag_age" -gt 900 ]; then
        echo "$(date '+%F %T') [DEADLETTER] ceo-opencode reason=dead_letter_push_B active_pid=$active_pid session_age_s=$session_age flag_age_s=$flag_age" >> "$SEAT_LOG"
        mkdir -p "$DEADLETTER_DIR" 2>/dev/null || true
        {
          echo "{"
          echo "  \"type\": \"seat_dead_letter\","
          echo "  \"active_pid\": $active_pid,"
          echo "  \"session_age_s\": $session_age,"
          echo "  \"flag_age_s\": $flag_age,"
          echo "  \"ts\": \"$(date -Iseconds)\""
          echo "}"
        } > "$DEADLETTER_DIR/dead-letter-seat-$(date '+%Y%m%d-%H%M%S')-$$.json" 2>/dev/null || true
        echo "[SKIP] ceo-opencode reason=dead_letter_push_B" >&2
        exec 9>&-
        return 1
      fi
      echo "$(date '+%F %T') [WAIT] ceo-opencode reason=another_session_active preempt_wait active_pid=$active_pid session_age_s=$session_age" >> "$SEAT_LOG"
    else
      echo "$(date '+%F %T') [WAIT] ceo-opencode reason=another_session_active active_pid=$active_pid session_age_s=$session_age" >> "$SEAT_LOG"
    fi
    echo "[SKIP] ceo-opencode reason=another_session_active" >&2
    exec 9>&-
    return 1
  fi
  local start_epoch task_hash
  start_epoch="$(date +%s)"
  SESSION_START_EPOCH="$start_epoch"
  SESSION_ACQUIRED_AT="$(date '+%F %T')"
  task_hash="$(printf '%s' "${TASK:-}" | sha256sum | awk '{print substr($1,1,16)}')"
  {
    echo "pid=$$"
    echo "start_epoch=$start_epoch"
    echo "task_hash=$task_hash"
  } > "$CLAIM_DIR/ceo-opencode.claim.$$"
  echo "pid=$$ task=${TASK:0:120} acquired_at=$SESSION_ACQUIRED_AT start_epoch=$start_epoch" > "$HOLDER_FILE"
  if [ -f "$FLAG_FILE" ]; then
    echo "$(date '+%F %T') [RUN] ceo-opencode lock_acquired shortlock_claim priority_task" >> "$SEAT_LOG"
  else
    echo "$(date '+%F %T') [RUN] ceo-opencode lock_acquired shortlock_claim" >> "$SEAT_LOG"
  fi
  exec 9>&-
  return 0
}

cleanup_claim() {
  # 仅删本会话 claim（pid 专属）与本会话 holder（pid 校验后才删）。
  rm -f "$CLAIM_DIR/ceo-opencode.claim.$$"
  local hpid
  hpid="$(sed -n 's/^pid=//p' "$HOLDER_FILE" 2>/dev/null | head -1 || true)"
  if [ "$hpid" = "$$" ]; then rm -f "$HOLDER_FILE"; fi
}

phase3_writeback() {
  # 阶段3 短锁回写：持短锁写回执 + 更新 holder；回执文件本身按会话唯一命名。
  local i ok=0
  exec 9>"$LOCK_FILE"
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if flock -n 9; then ok=1; break; fi
    sleep 0.2
  done

  PATROL_MTIME="$(stat -c %y /home/ubuntu/ai-team/ceo-seat/out/patrol-latest.md)"
  MODEL_SHA="$(sha256sum "$MODEL_OUT" | awk '{print $1}')"
  MODEL_BYTES="$(stat -c %s "$MODEL_OUT")"
  MODEL_ACTUAL="${MODEL:-${CHOSEN:-auto}}"
  # 2026-10-31 OPT 降级中间版：非 Zen 选用打标签并报警，不停摆；alert_push 自带 60 分钟去重
  case "${MODEL_ACTUAL}" in opencode/deepseek-v4.1-flash-free) DOWNGRADE_TAG=false;; *) DOWNGRADE_TAG=true;; esac
  if [ "$DOWNGRADE_TAG" = "true" ]; then /home/ubuntu/ai-team/scripts/alert_push.sh CEO_DOWNGRADE "大管家降级：选用脑 $MODEL_ACTUAL 非 Zen，任务仍继续出活，Zen 恢复前请知悉。" >> /home/ubuntu/ai-team/logs/ceo-seat.log 2>&1 || true; fi
  RECEIPT_TS="$(date '+%Y%m%d-%H%M%S')"
  RECEIPT="/home/ubuntu/ai-team/ceo-seat/out/revival-receipt-${RECEIPT_TS}-$$.md"
  CREATED_AT="$(date -Iseconds)"
  {
    echo "tool_name: ceo-opencode"
    echo "status: done"
    echo "task: ${TASK}"
    echo "model: ${MODEL_ACTUAL}"
    echo "downgrade: ${DOWNGRADE_TAG}"
    echo "downgrade_model: ${MODEL_ACTUAL}"
    echo "downgrade_reason: non_zen_selection_zen_unavailable_or_explicit_model"
    echo "session_mode: ${SESSION_MODE}"
    echo "handoff_file: ${HANDOFF_FILE}"
    echo "run_rc: ${RUN_RC}"
    echo "model_output_path: ${MODEL_OUT}"
    echo "model_output_bytes: ${MODEL_BYTES}"
    echo "sha_computed_by: deterministic_layer"
    echo "sha_label: 模型输出+确定性层算sha"
    echo "sha_note: output_sha256 is sha256sum of model_output_path computed by this shell script; it was NOT computed by the model, and the model was NOT asked to fabricate a sha."
    echo "output_sha256: ${MODEL_SHA}"
    echo "created_at: ${CREATED_AT}"
    echo "patrol_refreshed_at: ${PATROL_MTIME}"
    echo "patrol_layer: ${PATROL_LAYER}"
    echo "evidence: /home/ubuntu/ai-team/ceo-seat/out/patrol-latest.md"
    echo "acceptance: passed"
  } > "$RECEIPT"
  # 更新 holder（含回执路径与完成时间；trap 在退出时仍会清理 holder）
  echo "pid=$$ task=${TASK:0:120} acquired_at=${SESSION_ACQUIRED_AT:-} start_epoch=${SESSION_START_EPOCH:-} completed_at=$(date '+%F %T') receipt=$RECEIPT status=done" > "$HOLDER_FILE"
  if [ "$ok" = "1" ]; then
    exec 9>&-
  else
    echo "$(date '+%F %T') [WARN] ceo-opencode phase3_lock_busy receipt_written_without_lock receipt=$RECEIPT" >> "$SEAT_LOG"
  fi
}

# 沙箱测试入口：仅定义函数、不跑主流程（生产执行不设此变量）。
if [ "${CEO_OPENCODER_SOURCED:-}" = "1" ]; then return 0 2>/dev/null || exit 0; fi

# 参数解析（旧逻辑前移：phase1 需要 TASK 算 task_hash）
MODEL=""
TASK=""
while [ $# -gt 0 ]; do case "$1" in --model) MODEL="$2"; shift 2;; *) TASK="${TASK:+$TASK }$1"; shift;; esac; done

phase1_claim || exit 1
trap cleanup_claim EXIT

# --- 2026-10-28 resident session layer: write a handoff file, never hard-continue a dead session ---
SESSION_MODE="fresh"
HANDOFF_FILE="/home/ubuntu/ai-team/state/butler-seat-revive/session-handoff-$(date '+%Y%m%d-%H%M%S').json"
HANDOFF_TASK="$TASK" python3 /home/ubuntu/ai-team/scripts/butler_session_handoff.py "$HANDOFF_FILE" 2>/dev/null || true
# 2026-11-01 ARCHFIX: 精简摘要此前无任何调用方，会话每轮读到的是 15 小时前的旧状态（摘要 pending=23，实际=70）。
# 每轮开跑前重算一次，保证"只读摘要"与本轮八层审查同源；失败不阻塞会话。
python3 /home/ubuntu/ai-team/scripts/butler_slim_summary.py >/dev/null 2>&1 || true
echo "$(date '+%F %T') ACK model=${MODEL:-auto} task=${TASK:0:100}" >> /home/ubuntu/ai-team/state/dispatch-ack.log
echo "$(date '+%F %T') ACK model=${MODEL:-auto} task=${TASK:0:100}" >> /home/ubuntu/ai-team/ceo-seat/out/dispatch-ack-copy.log
ARGS=(run)
[ -n "$MODEL" ] && ARGS+=(--model "$MODEL")
# --- 2026-10-28 runner2: capture model stdout to a file (deterministic-layer input) ---
RUN_TS="$(date '+%Y%m%d-%H%M%S')"
MODEL_OUT="/home/ubuntu/ai-team/ceo-seat/out/model-output-${RUN_TS}-$$.txt"
: > "$MODEL_OUT"
run_once() {
  if [ "$1" = "opencode/deepseek-v4.1-flash-free" ]; then
    if [ "${ZEN_PROXY_PORT:-10815}" = "direct" ]; then env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy timeout 600 opencode run --model "$1" "$TASK" </dev/null > "$MODEL_OUT" 2>&1; return; fi
    timeout 600 env HTTP_PROXY=http://127.0.0.1:${ZEN_PROXY_PORT:-10815} HTTPS_PROXY=http://127.0.0.1:${ZEN_PROXY_PORT:-10815} http_proxy=http://127.0.0.1:${ZEN_PROXY_PORT:-10815} https_proxy=http://127.0.0.1:${ZEN_PROXY_PORT:-10815} opencode run --model "$1" "$TASK" </dev/null > "$MODEL_OUT" 2>&1
  else
    timeout 600 opencode run --model "$1" "$TASK" </dev/null > "$MODEL_OUT" 2>&1
  fi
}
if [ -n "$MODEL" ]; then
  timeout 600 opencode "${ARGS[@]}" "$TASK" </dev/null > "$MODEL_OUT" 2>&1
else
  # 2026-10-30对齐修正：席位走opencode run，可用模型只能取opencode provider配置（动态读配置，不写死）；
  # 网关route的模型大多不在opencode配置里，15:12/15:17/15:32三轮复活均因此 NO_HEALTHY_MODEL 空跑，已修正。
  ROUTE_MODELS=$(python3 -c "import json; c=json.load(open('/home/ubuntu/.config/opencode/opencode.json')); mg=(c.get('provider') or {}).get('my-gateway') or {}; print(' '.join((mg.get('models') or {}).keys()))" 2>/dev/null)
  CHOSEN=""
  # 2026-10-31 ZENDIRECT: Zen direct works, residential proxy times out, probe Zen direct first
for _zp in 10815 10801 10803 10812 10814 10802 10804 10810 10813; do
    if (cd /tmp && timeout 45 env HTTP_PROXY=http://127.0.0.1:$_zp HTTPS_PROXY=http://127.0.0.1:$_zp http_proxy=http://127.0.0.1:$_zp https_proxy=http://127.0.0.1:$_zp opencode run --model "opencode/deepseek-v4.1-flash-free" "只回：收到" </dev/null 2>/dev/null | grep -q "收到"); then CHOSEN="opencode/deepseek-v4.1-flash-free"; ZEN_PROXY_PORT="$_zp"; break; fi
  done
  if [ -z "$CHOSEN" ] && (cd /tmp && env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy timeout 45 opencode run --model "opencode/deepseek-v4.1-flash-free" "只回：收到" </dev/null 2>/dev/null | grep -q "收到"); then CHOSEN="opencode/deepseek-v4.1-flash-free"; ZEN_PROXY_PORT="direct"; fi
  # 2026-11-07 BUTLER-FIX: muse-reverse/muse 是远端会话，无枢纽文件访问权限，选它只会出 yanzi 空答（2026-11-07 06:12-08:09 连 14 轮）；大管家必须用本地带文件工具的模型，Zen 死时直接落网关，不经过 muse-reverse。
  for _mid in $ROUTE_MODELS; do
    [ -n "$CHOSEN" ] && break
    _cand="my-gateway/$_mid"
    if (cd /tmp && timeout 45 opencode run --model "$_cand" "只回：收到" </dev/null 2>/dev/null | grep -q "收到"); then CHOSEN="$_cand"; break; fi
  done
  if [ -z "$CHOSEN" ]; then
    echo "$(date '+%F %T') ceo-opencode NO_HEALTHY_MODEL route_empty" >> /home/ubuntu/ai-team/logs/ceo-seat.log
    exit 1
  fi
  echo "$(date '+%F %T') brain=$CHOSEN" >> /home/ubuntu/ai-team/state/brain-active.log
  if run_once "$CHOSEN"; then SESSION_MODE="fresh"; else
    _ok=0
    # 2026-11-07 BUTLER-FIX: 不重试 muse-reverse（远端会话，无文件权限，必出 yanzi 空答）
    for _mid in $ROUTE_MODELS; do
      _cand="my-gateway/$_mid"
      [ "$_cand" = "$CHOSEN" ] && continue
      if run_once "$_cand"; then _ok=1; CHOSEN="$_cand"; break; fi
    done
    if [ $_ok -eq 1 ]; then SESSION_MODE="fresh"; else SESSION_MODE="fresh"; timeout 600 opencode run --model "$CHOSEN" "$TASK [会话交接：上一会话已断，本轮为新会话，不硬续旧会话。先读交接文件 $HANDOFF_FILE 与巡航，再承接未完成任务。]" > "$MODEL_OUT" 2>&1; fi
  fi
fi

# --- 2026-10-28 runner2 completion: deterministic layer computes sha of MODEL OUTPUT, never of wrapper body ---
RUN_RC=$?
if [ "$RUN_RC" -ne 0 ]; then
  echo "$(date '+%F %T') ceo-opencode completion FAILED run_rc=$RUN_RC no_receipt model_output=${MODEL_OUT}" >> /home/ubuntu/ai-team/logs/ceo-seat.log
  exit "$RUN_RC"
fi
if [ ! -s "$MODEL_OUT" ]; then
  echo "$(date '+%F %T') ceo-opencode completion FAILED model_output_empty_or_missing model_output=${MODEL_OUT} no_receipt" >> /home/ubuntu/ai-team/logs/ceo-seat.log
  exit 1
fi
if python3 /home/ubuntu/ai-team/scripts/patrol_deterministic.py >> /home/ubuntu/ai-team/logs/patrol-det.log 2>&1; then
  PATROL_LAYER="patrol_deterministic.py"
elif python3 /home/ubuntu/ai-team/scripts/patrol_collect_v2.py --base /home/ubuntu/ai-team --out /home/ubuntu/ai-team/ceo-seat/out/patrol-latest.md --sort-log /home/ubuntu/ai-team/state/priority/sort-log.jsonl --mode collect >> /home/ubuntu/ai-team/logs/patrol-collect-v2.log 2>&1; then
  PATROL_LAYER="patrol_collect_v2.py (fallback)"
else
  echo "$(date '+%F %T') ceo-opencode completion FAILED patrol_refresh_failed no_receipt" >> /home/ubuntu/ai-team/logs/ceo-seat.log
  exit 1
fi
phase3_writeback
echo "$(date '+%F %T') ceo-opencode completion OK receipt=${RECEIPT} output_sha256=${MODEL_SHA} model_output=${MODEL_OUT}" >> /home/ubuntu/ai-team/logs/ceo-seat.log
exit 0
