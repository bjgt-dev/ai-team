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
# 知识库收件箱须有 10 分钟内的新抽取文件，证明对话记录正在实时进枢纽；
# 超过 10 分钟无新文件即 RED（抽取停了，实时写断了）。
say "--- 实时写入管线 ---"
NEWEST=$(ssh "$HUB" "ls -t $HUB_BASE/knowledge/收件箱/extract-*.md 2>/dev/null | head -1")
if [ -z "$NEWEST" ]; then
  red "实时写入管线无抽取文件（收件箱为空，S5 可能停了）"
else
  AGE=$(ssh "$HUB" "echo \$(( \$(date +%s) - \$(stat -c %Y '$NEWEST') ))")
  if [ "$AGE" -le 600 ]; then
    ok "实时写入管线在跑（最新抽取 $(basename "$NEWEST")，${AGE}s 前）"
  else
    red "实时写入管线疑似停了（最新抽取 $(basename "$NEWEST")，${AGE}s 前，超 10 分钟）"
  fi
fi

# 4. 注册表漂移校验
say "--- 注册表漂移 ---"
if ssh "$HUB" "python3 $HUB_BASE/scripts/hub-registry-check.py" 2>&1; then
  ok "注册表无漂移"
else
  red "注册表有漂移（见上文，收据 state/logs/hub-registry-drift.log）"
fi

if [ "$RED" = 0 ]; then say "RESULT: GREEN"; else say "RESULT: RED"; fi
exit "$RED"
