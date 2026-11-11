#!/bin/bash
# 大管家脑通道看门狗 v2（2026-10-23 23:00）：每 5 分钟实测脑通道；Zen 免费额度按出口 IP 限额——当前出口被限即按 brain-proxy.env 轮换名单自动切换，全部出口被限才告警。结果落 state/brain-health.md
set -u
exec 9>/tmp/brain_watch.lock
flock -n 9 || exit 0
ENVF=/home/ubuntu/ai-team/state/brain-proxy.env
[ -f "$ENVF" ] && . "$ENVF"
CUR=${BRAIN_PROXY_PORT:-10814}
ROT=${BRAIN_PROXY_ROTATION:-"10815 10812 10814"}
STATE=/home/ubuntu/ai-team/state/brain-health.md
alert() {
TOKEN=$(sudo -n docker exec openclaw-official-openclaw-gateway-1 cat /home/node/.openclaw/.telegram-bot-token 2>/dev/null)
if [ -n "$TOKEN" ]; then
curl -s -m 15 -X POST "https://api.telegram.org/bot$TOKEN/sendMessage" -d chat_id=8636663583 -d text="$1" >/dev/null 2>&1
fi
echo "$(date '+%F %T') ALERT: $1" >> /home/ubuntu/ai-team/state/brain-alerts.log
}
probe_port() {
OUT=$(cd /tmp && env HTTP_PROXY=http://127.0.0.1:$1 HTTPS_PROXY=http://127.0.0.1:$1 http_proxy=http://127.0.0.1:$1 https_proxy=http://127.0.0.1:$1 NO_PROXY=127.0.0.1,localhost timeout 45 opencode run --model opencode/deepseek-v4.1-flash-free "只回两个字：收到" </dev/null 2>&1 | tail -c 400)
if echo "$OUT" | grep -q "Free usage limit reached"; then echo LIMITED;
elif echo "$OUT" | grep -q "收到"; then echo OK;
else echo DEAD; fi
}
canary() {
OUT=$(cd /tmp && timeout 60 opencode run --model "$1" "只回两个字：收到" </dev/null 2>/dev/null | tail -c 200)
if echo "$OUT" | grep -q "收到"; then echo OK; else echo DEAD; fi
}
# --- Zen 主脑：当前出口探活，被限/死亡则轮换 ---
# 2026-11-01 OPT: 单次探活超时不直接判死。opencode build 本身常超45秒，
# 单次失败可能是抖动/慢启动，不是Zen死。连续2次失败才判死并轮换，
# 第1次只记计数、保持上一状态，避免状态震荡和误切口。
FAILF=/home/ubuntu/ai-team/state/brain-watch-zen-fail-count
ZEN=$(probe_port "$CUR")
# 2026-10-31 DIRECT: 代理死不等于 Zen 死，先直连探一次，直连通则 Zen=OK(direct)，不误报 DEAD
ZENDIRECT=$(cd /tmp && env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy timeout 45 opencode run --model opencode/deepseek-v4.1-flash-free "只回两个字：收到" </dev/null 2>&1 | tail -c 400)
if echo "$ZENDIRECT" | grep -q "收到"; then ZEN="OK(direct)"; fi
# 2026-10-31 OPT: OK 按前缀判，OK 与 OK(direct) 都算 OK
case "$ZEN" in OK*) ZEN_OK=1; echo 0 > "$FAILF" 2>/dev/null;; *) ZEN_OK=0;; esac
SWITCHED=""
if [ "$ZEN_OK" = "0" ]; then
  CNT=$(cat "$FAILF" 2>/dev/null || echo 0)
  CNT=$((CNT+1))
  echo "$CNT" > "$FAILF"
  if [ "$CNT" -lt 2 ]; then
    ZEN="PENDING_CONFIRM(fail $CNT/2)"
  else
    echo 2 > "$FAILF"
  fi
fi
if [ "$ZEN_OK" = "0" ]; then
  if [ "${CNT:-0}" -ge 2 ]; then
  for P in $ROT; do
    [ "$P" = "$CUR" ] && continue
    R=$(probe_port "$P")
    if [ "$R" = "OK" ]; then
      printf 'BRAIN_PROXY_PORT=%s\nBRAIN_PROXY_ROTATION="%s"\nBRAIN_PROXY_LAST_SWITCH=%s\n' "$P" "$ROT" "$(date +%s)" > "$ENVF"
      SWITCHED="脑出口 $CUR 被限/无响应（$ZEN），已自动切换到 $P 并探活通过。"
      alert "大管家$SWITCHED"
      CUR=$P
      ZEN=OK
      ZEN_OK=1
      break
    fi
  done
  fi
fi
export HTTP_PROXY=http://127.0.0.1:$CUR HTTPS_PROXY=http://127.0.0.1:$CUR http_proxy=http://127.0.0.1:$CUR https_proxy=http://127.0.0.1:$CUR NO_PROXY=127.0.0.1,localhost
# 2026-10-31 OPT: Go 历史记法已作废，不再探活
GO="历史记法已作废，不再探活"
KEY=$(cat /home/ubuntu/shared/worker-kit/newapi.key)
OSS=$(curl -s -m 30 -o /dev/null -w "%{http_code}" http://127.0.0.1:3001/v1/chat/completions -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{"model":"glm-4-flash-cc","messages":[{"role":"user","content":"回:OK"}],"max_tokens":10}')
DS=$(curl -s -m 30 -o /dev/null -w "%{http_code}" http://127.0.0.1:3001/v1/chat/completions -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"回:OK"}],"max_tokens":10}')
PREVZEN_RAW=$(grep "Zen 通道" "$STATE" 2>/dev/null | sed 's/.*：//')
# 2026-10-31 OPT: PREVZEN 与 OK 前缀判同步，OK(direct) 归一为 OK
case "$PREVZEN_RAW" in OK*|PENDING*) PREVZEN=OK;; DEAD*) PREVZEN=DEAD;; LIMITED*) PREVZEN=LIMITED;; *) PREVZEN="";; esac
{
echo "# 脑通道健康（brain_watch 每 5 分钟更新）"
echo "- 时间：$(date '+%F %T')"
echo "- 当前脑出口：127.0.0.1:$CUR（轮换名单：$ROT）"
echo "- Zen 通道 opencode/deepseek-v4.1-flash-free（当前出口实测）：$ZEN"
echo "- Go 通道：历史记法已作废，不再探活（旧称 Go 订阅通道已作废，只有 Zen）"
echo "- 网关兜底 glm-4-flash-cc（newapi 直测）：HTTP $OSS"
echo "- 网关兜底 deepseek-chat（newapi 直测）：HTTP $DS（死通道已禁用，预期 model_not_found）"
echo "- 席位最近选用脑：$(grep -v "patrol brain=" /home/ubuntu/ai-team/state/brain-active.log 2>/dev/null | tail -1 || echo 无记录)"
} > "$STATE"
if [ "$ZEN_OK" = "0" ] && [ "$PREVZEN" = "OK" ]; then
alert "大管家强脑不可用：Zen 状态 $ZEN（当前出口 $CUR，轮换名单已试完），glm 直测 HTTP $OSS。席位将自动落到弱脑兜底，判断质量下降，请知悉。"
fi
