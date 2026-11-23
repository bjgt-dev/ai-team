#!/bin/bash
# wake-up-check.sh — 本席唤醒第一动作（AGENTS.md 2026-11-01 董事长强制令）。
# 2026-11-10 补：唤醒拉取 = 六件 + 注册表 + 状态板；漂移校验进体检。
# 体检项：ssh 出站配置、席位实选脑、Zen 健康、决策队列 pending 数、
# 根盘/数据盘、五步链新鲜度、注册表漂移。
# 任一异常即 RED（exit 1），全绿才许说“系统健康”。
# 拉取规则（2026-11-09 截断事故教训）：先验 ssh 通、源文件存在，再拉；
# 拉到临时文件、比对 sha、本地不同先备份再覆盖；绝不裸 `>` 重定向到 live 文件。
# 只读检查 + 安全拉取，不改生产数据、不动队列、不派任务。
HUB="arm-agent"
HUB_BASE="/home/ubuntu/ai-team"
RED=0
TS=$(date +%Y%m%d-%H%M)

say() { printf '%s\n' "$*"; }
red() { RED=1; say "RED：$*"; }
ok() { say "OK ：$*"; }

# 1. ssh 出站配置（12 次丢失重建的高频坑）
if [ -f /root/.ssh/config ] && grep -q "Include /home/hatch/.ssh/config" /root/.ssh/config; then
  ok "ssh 配置在位"
else
  printf 'Include /home/hatch/.ssh/config\n' > /root/.ssh/config 2>/dev/null \
    && grep -q "Include /home/hatch/.ssh/config" /root/.ssh/config \
    && ok "ssh 配置已重建" || { red "ssh 配置重建失败"; }
fi
if ssh -o BatchMode=yes -o ConnectTimeout=10 "$HUB" true 2>/dev/null; then
  ok "ssh arm-agent 通"
else
  red "ssh arm-agent 不通（先修出站配置，再拉取）"
  say "RESULT: RED"
  exit 1
fi

# 2. 枢纽体检五项（hub 侧 wake-check-hub.py，队列已修为 ceo-seat 正典路径）
say "--- 枢纽体检 ---"
if ssh "$HUB" "python3 $HUB_BASE/scripts/wake-check-hub.py" 2>&1; then
  ok "枢纽体检全绿"
else
  red "枢纽体检有 RED 项（见上文）"
fi

# 3. 安全拉取：六件 + 注册表 + 状态板
# 2026-11-18 D3 修：MEMORY.md 受运行时保护无法覆盖（cp/mv 均 EPERM），
# 故 MEMORY.md 只比对不覆盖；其余文件用 cp 原地覆盖，失败即 RED（不再静默假绿）。
say "--- 拉取最新 ---"
pull_file() {
  local remote="$1" local_dest="$2" tmp
  tmp=$(mktemp)
  if ! ssh "$HUB" "test -e '$remote'" 2>/dev/null; then
    red "枢纽缺文件，跳过拉取：$remote"; rm -f "$tmp"; return
  fi
  if ! scp -q "$HUB:$remote" "$tmp" 2>/dev/null; then
    red "拉取失败：$remote"; rm -f "$tmp"; return
  fi
  if [ -f "$local_dest" ]; then
    if cmp -s "$tmp" "$local_dest"; then
      ok "未变：$(basename "$local_dest")"
      rm -f "$tmp"; return
    fi
    # MEMORY.md / AGENTS.md 受运行时保护，无法覆盖：只比对，不写；不一致即 RED。
    # 2026-11-18 董事长令：席位运行时直接读枢纽正本，注入的本地旧份冲突时作废。
    if [ "$(basename "$local_dest")" = "MEMORY.md" ] || [ "$(basename "$local_dest")" = "AGENTS.md" ]; then
      red "$(basename "$local_dest") 与枢纽不一致（本地受运行时保护无法覆盖，运行时须直接读枢纽正本）；本地 $(wc -c <"$local_dest") 字节，枢纽 $(wc -c <"$tmp") 字节，sha 本地 $(sha256sum "$local_dest" | cut -c1-12)，枢纽 $(sha256sum "$tmp" | cut -c1-12)"
      rm -f "$tmp"; return
    fi
    cp "$local_dest" "$local_dest.bak-$TS" 2>/dev/null || { red "备份失败，拒覆盖：$local_dest"; rm -f "$tmp"; return; }
  fi
  mkdir -p "$(dirname "$local_dest")"
  if cp "$tmp" "$local_dest" 2>/dev/null; then
    rm -f "$tmp"
    ok "已拉取：$(basename "$local_dest")"
  else
    red "覆盖失败（写不进）：$local_dest"
    rm -f "$tmp"
  fi
}
for f in SOUL.md IDENTITY.md USER.md MEMORY.md AGENTS.md TOOLS.md; do
  pull_file "$HUB_BASE/state/twt-core-backup/$f" "$HOME/$f"
done
pull_file "$HUB_BASE/state/hub-registry.json" "$HOME/workspace/tools/hub-registry.json"
pull_file "$HUB_BASE/state/status-board.md" "$HOME/workspace/tools/status-board.md"

# 3b. 枢纽唯一真源门禁（2026-11-18 董事长令，代码保证）
# 席位运行时必须读过当前枢纽 MEMORY.md；标记过期或枢纽已变即 RED，
# 须先读枢纽正本并打标记（hub-truth-gate.sh --mark）再答事实性问题。
say "--- 真源门禁 ---"
if bash "$HOME/workspace/tools/hub-truth-gate.sh" 2>&1; then
  ok "真源门禁通过（已读当前枢纽 MEMORY.md）"
else
  red "真源门禁未过（见上文）；先读枢纽正本并打标记再答"
fi

# 3c. 实时写入管线健康（S5 自动抽取，2026-11-18 董事长令，代码保证）
# 2026-11-23 决定 13 修（旧判据误报，item6 复核 + 本轮实测）：
#   旧判据“收件箱最新抽取文件 ≤10 分钟”把“文件晚到”等同于“管线停了”，
#   闲时（无新行可抽则不写文件）与网关抖动时必误报。实测 2026-11-23：
#   12:35→12:57 间隔 22 分钟报 RED，其间抽取器 cron 每次都跑，只是网关
#   两模型均超时（日志连串 timed out），失败后 state 未动、重试即恢复。
#   新判据只认两条硬信号，文件新鲜度只作参考信息，不再判 RED：
#   ① 抽取器在跑：/etc/cron.d/gate-extractor 在位，且 gate-extractor.log
#      15 分钟内有写入（每 5 分钟一跑，成功/失败/无新行都会写一行；
#      15 分钟容 2 次漏跑 + 抖动）；
#   ② 无积压：raw_log 待抽取行 pending = max(seq) - last_seq ≤ 400
#      （两批，下一两跑即清；last_seq 取 state/gate/extractor-state.json）。
#   任一不满足即 RED（并报实测数）；DB/状态读不到时按“积压未核实” RED，不猜绿。
say "--- 实时写入管线 ---"
S5_CRON=$(ssh "$HUB" "test -e /etc/cron.d/gate-extractor && echo yes || echo no")
S5_LOG_AGE=$(ssh "$HUB" "echo \$(( \$(date +%s) - \$(stat -c %Y $HUB_BASE/state/gate/gate-extractor.log 2>/dev/null || echo 0) ))")
S5_LAST_SEQ=$(ssh "$HUB" "grep -o '[0-9]*' $HUB_BASE/state/gate/extractor-state.json 2>/dev/null | head -1")
S5_NEWEST=$(ssh "$HUB" "ls -t $HUB_BASE/knowledge/收件箱/extract-*.md 2>/dev/null | head -1")
if [ -n "$S5_NEWEST" ]; then
  S5_NEWEST_AGE=$(ssh "$HUB" "echo \$(( \$(date +%s) - \$(stat -c %Y '$S5_NEWEST') ))")
  say "最新抽取 $(basename "$S5_NEWEST")，${S5_NEWEST_AGE}s 前（仅参考，不判 RED）"
else
  say "收件箱无抽取文件（仅参考，不判 RED）"
fi
S5_RUNNING=0
if [ "$S5_CRON" = "yes" ] && [ -n "$S5_LOG_AGE" ] && [ "$S5_LOG_AGE" -le 900 ]; then S5_RUNNING=1; fi
S5_BACKLOG_OK=0; S5_PENDING="?"
S5_MAX_SEQ=$(ssh "$HUB" "docker exec ops-pg psql -U postgres -d central_queue -t -A -c 'SELECT max(seq) FROM raw_log;' 2>/dev/null" | tr -d '[:space:]')
if [ -n "$S5_LAST_SEQ" ] && [ -n "$S5_MAX_SEQ" ] && [ "$S5_MAX_SEQ" -ge "$S5_LAST_SEQ" ] 2>/dev/null; then
  S5_PENDING=$((S5_MAX_SEQ - S5_LAST_SEQ))
  if [ "$S5_PENDING" -le 400 ]; then S5_BACKLOG_OK=1; fi
fi
if [ "$S5_RUNNING" -eq 1 ] && [ "$S5_BACKLOG_OK" -eq 1 ]; then
  ok "实时写入管线正常（抽取器在跑，日志 ${S5_LOG_AGE}s 前写；无积压，待抽取 ${S5_PENDING} 行，last_seq=${S5_LAST_SEQ}）"
else
  [ "$S5_RUNNING" -eq 1 ] || red "实时写入管线：抽取器疑似未跑（cron 在位：$S5_CRON，日志 ${S5_LOG_AGE}s 前写，超 15 分钟）"
  if [ "$S5_BACKLOG_OK" -eq 1 ]; then
    say "积压：无（待抽取 ${S5_PENDING} 行）"
  elif [ "$S5_PENDING" = "?" ]; then
    red "实时写入管线：积压未核实（last_seq=${S5_LAST_SEQ:-未知}，max(seq) 查不到，DB/状态读失败）"
  else
    S5_OLDEST=$(ssh "$HUB" "docker exec ops-pg psql -U postgres -d central_queue -t -A -c \"SELECT to_char(min(ts),'YYYY-MM-DD HH24:MI') FROM raw_log WHERE seq > $S5_LAST_SEQ;\" 2>/dev/null" | tr -d '\r\n')
    red "实时写入管线：有积压（待抽取 ${S5_PENDING} 行，last_seq=${S5_LAST_SEQ}，最旧未抽取行 ${S5_OLDEST:-未知}；抽取器在跑：$([ "$S5_RUNNING" -eq 1 ] && echo 是 || echo 否)）"
  fi
fi

# 4. 注册表漂移校验
say "--- 注册表漂移 ---"
if ssh "$HUB" "python3 $HUB_BASE/scripts/hub-registry-check.py" 2>&1; then
  ok "注册表无漂移"
else
  red "注册表有漂移（见上文，收据 state/logs/hub-registry-drift.log）"
fi

# 5. 同步守护自愈（2026-11-23 决定12 补：tools/*.sh 已纳入 hub-sync 自动同步，
# 守护须在跑同步才真实发生；11-18 改版漏了此步，hub-sync-daemon.sh 头注仍写“唤醒时确保运行”。
# best-effort：起不来只提示，不影响 RED/GREEN 与退出码。）
say "--- 同步守护 ---"
mkdir -p "$HOME/workspace/.sync" 2>/dev/null
if [ -f "$HOME/workspace/tools/hub-sync-daemon.sh" ]; then
  if bash "$HOME/workspace/tools/hub-sync-daemon.sh" start >> "$HOME/workspace/.sync/daemon.log" 2>&1; then
    ok "同步守护已确保运行"
  else
    say "同步守护启动失败（下次唤醒重试，不影响唤醒结果）"
  fi
else
  say "同步守护脚本不在，跳过启动"
fi

if [ "$RED" = 0 ]; then say "RESULT: GREEN"; else say "RESULT: RED"; fi
# 唤醒完成标记（供 seat-wake-gate.sh 查：跑过即写，不论 GREEN/RED；RED 是状态不是没跑）
# 2026-11-23 决定12 补：11-18 改版漏了此块，枢纽全树只有 seat-wake-gate.sh 读它、无他处写它；
# 不补则标记过期后 seat-wake-gate 恒 FAIL，答前门禁全断。
mkdir -p "$HOME/workspace/.gates"
{
  echo "ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "epoch=$(date +%s)"
  echo "result=$([ "$RED" = 0 ] && echo GREEN || echo RED)"
} > "$HOME/workspace/.gates/wake.done"
exit "$RED"
