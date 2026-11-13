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
    cp "$local_dest" "$local_dest.bak-$TS" 2>/dev/null || { red "备份失败，拒覆盖：$local_dest"; rm -f "$tmp"; return; }
  fi
  mkdir -p "$(dirname "$local_dest")"
  mv "$tmp" "$local_dest" && ok "已拉取：$(basename "$local_dest")"
}
for f in SOUL.md IDENTITY.md USER.md MEMORY.md AGENTS.md TOOLS.md; do
  pull_file "$HUB_BASE/state/twt-core-backup/$f" "$HOME/$f"
done
pull_file "$HUB_BASE/state/hub-registry.json" "$HOME/workspace/tools/hub-registry.json"
pull_file "$HUB_BASE/state/status-board.md" "$HOME/workspace/tools/status-board.md"

# 4. 注册表漂移校验
say "--- 注册表漂移 ---"
if ssh "$HUB" "python3 $HUB_BASE/scripts/hub-registry-check.py" 2>&1; then
  ok "注册表无漂移"
else
  red "注册表有漂移（见上文，收据 state/logs/hub-registry-drift.log）"
fi

# 5. 同步守护（2026-11-13 hub-sync；best-effort，不影响 RED/GREEN 与退出码）
# 机制跟着席位走：同步脚本正本在枢纽，唤醒时拉取最新并确保守护运行；
# 换账号后新持有人跑一次唤醒，同步即恢复，零额外操作。
say "--- 同步守护 ---"
SYNC_DIR="$HOME/workspace/.sync"
mkdir -p "$SYNC_DIR" 2>/dev/null
# 安全拉取（与 pull_file 同款做法，但绝不写 RED：拉不到就跳过）
pull_sync_file() {
  local remote="$1" local_dest="$2" tmp
  tmp=$(mktemp)
  if ! ssh "$HUB" "test -e '$remote'" 2>/dev/null; then
    say "同步脚本未在枢纽，跳过：$(basename "$remote")"; rm -f "$tmp"; return 0
  fi
  if ! scp -q "$HUB:$remote" "$tmp" 2>/dev/null; then
    say "同步脚本拉取失败，跳过：$(basename "$remote")"; rm -f "$tmp"; return 0
  fi
  if [ -f "$local_dest" ] && cmp -s "$tmp" "$local_dest"; then rm -f "$tmp"; return 0; fi
  if [ -f "$local_dest" ]; then cp "$local_dest" "$local_dest.hubsync-bak-$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true; fi
  mkdir -p "$(dirname "$local_dest")"
  if mv "$tmp" "$local_dest" 2>/dev/null; then ok "同步脚本已更新：$(basename "$local_dest")"; else say "同步脚本更新失败：$(basename "$local_dest")"; fi
  return 0
}
pull_sync_file "$HUB_BASE/scripts/hub-sync.sh" "$HOME/workspace/tools/hub-sync.sh"
pull_sync_file "$HUB_BASE/scripts/hub-sync-daemon.sh" "$HOME/workspace/tools/hub-sync-daemon.sh"
if [ -f "$HOME/workspace/tools/hub-sync-daemon.sh" ]; then
  if bash "$HOME/workspace/tools/hub-sync-daemon.sh" start >> "$SYNC_DIR/daemon.log" 2>&1; then
    ok "同步守护已确保运行"
  else
    say "同步守护启动失败（下次唤醒重试，不影响唤醒结果）"
  fi
else
  say "同步守护脚本不在，跳过启动"
fi

if [ "$RED" = 0 ]; then say "RESULT: GREEN"; else say "RESULT: RED"; fi
# 唤醒完成标记（供 seat-wake-gate.sh 查：跑过即写，不论 GREEN/RED；RED 是状态不是没跑）
mkdir -p "$HOME/workspace/.gates"
{
  echo "ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "epoch=$(date +%s)"
  echo "result=$([ "$RED" = 0 ] && echo GREEN || echo RED)"
} > "$HOME/workspace/.gates/wake.done"
exit "$RED"
