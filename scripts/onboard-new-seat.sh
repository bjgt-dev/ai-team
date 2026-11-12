#!/bin/bash
# onboard-new-seat.sh -- 新席位上岗标准化脚本 v2.0（2026-11-10）
#
# 用途：一个全新的 Muse 账号/新机器（白纸，没当过任何席位）上岗为某席位时，
# 一次性执行到位。处理新机器上缺失的全部东西：SSH 密钥、SSH 配置、
# 代理脚本、Tailscale 入网、SSH 测试。
#
# 用法（新账号直接执行，一行就行）：
#   bash onboard-new-seat.sh
# 脚本会：生成密钥 → 写 SSH 配置 → 装代理脚本 → 查 Tailscale →
# 若 Tailscale 未连，跑 tailscale up 并打印批准链接，等董事长批准 →
# 测 SSH → 打印公钥（董事长加进枢纽）→ 再测 SSH。
# 董事长须做两件事：①批准 Tailscale 新设备 ②把公钥加进枢纽 authorized_keys。
# 两件都做完，脚本再跑一次即通。
#
# 依赖：本机须能访问 egress 代理（Muse 平台自带 HTTPS_PROXY）。
# 枢纽：arm-agent，127.0.0.1，ubuntu。
set -u
HUB_HOST="arm-agent"
HUB_IP="127.0.0.1"
HUB_USER="ubuntu"
PROXY="/home/hatch/bin/ts-proxy-noauth.sh"

# ssh 实际读的配置位置：root 跑就读 /root/.ssh/config，否则读 $HOME/.ssh/config。
# 两边都写一份并软链，保证不管谁跑都对。
SSH_DIR_HOME="$HOME/.ssh"
SSH_DIR_ROOT="/root/.ssh"
if [ "$(id -u)" = "0" ]; then
  SSH_DIR="$SSH_DIR_ROOT"
else
  SSH_DIR="$SSH_DIR_HOME"
fi
KEY="$SSH_DIR/id_ed25519"
CONFIG="$SSH_DIR/config"

echo "== 新席位上岗（新机器一次性执行） =="
echo "本机用户：$(whoami)（uid $(id -u)），SSH 配置位置：$CONFIG"

echo "[1/6] 生成 SSH 密钥..."
mkdir -p "$SSH_DIR" "$SSH_DIR_HOME" "$SSH_DIR_ROOT" 2>/dev/null || true
chmod 700 "$SSH_DIR" 2>/dev/null || true
if [ ! -f "$KEY" ]; then
  ssh-keygen -t ed25519 -f "$KEY" -N "" -q || { echo "FAIL：密钥生成失败"; exit 1; }
  echo "密钥已生成：$KEY"
else
  echo "密钥已存在，跳过生成。"
fi
# root 与 $HOME 两边软链，保证 ssh 不管以谁跑都读到同一份。
# 只给“另一边”建软链，绝不把密钥文件本身链向自己（会成符号链接循环，毁掉真实密钥）。
# 目标位置已有真实文件时不动它（不覆盖真实密钥）。
link_key() {
  local src="$1" dst="$2"
  if [ -f "$dst" ] && [ ! -L "$dst" ]; then
    echo "  $dst 已是真实文件，不覆盖。"
    return 0
  fi
  ln -sfn "$src" "$dst" 2>/dev/null || true
}
if [ "$SSH_DIR" = "$SSH_DIR_ROOT" ]; then
  link_key "$KEY" "$SSH_DIR_HOME/id_ed25519"
  link_key "$KEY.pub" "$SSH_DIR_HOME/id_ed25519.pub"
else
  link_key "$KEY" "$SSH_DIR_ROOT/id_ed25519"
  link_key "$KEY.pub" "$SSH_DIR_ROOT/id_ed25519.pub"
fi
ln -sf "$KEY.pub" "$SSH_DIR_HOME/id_ed25519.pub" 2>/dev/null || true
ln -sf "$KEY.pub" "$SSH_DIR_ROOT/id_ed25519.pub" 2>/dev/null || true

echo "[2/6] 写 SSH 配置..."
cat > "$CONFIG" <<EOF
Host arm-agent
    HostName $HUB_IP
    User $HUB_USER
    ProxyCommand $PROXY %h %p
    IdentityFile $KEY
    StrictHostKeyChecking accept-new
    ConnectTimeout 30
EOF
chmod 600 "$CONFIG"
ln -sf "$CONFIG" "$SSH_DIR_HOME/config" 2>/dev/null || true
ln -sf "$CONFIG" "$SSH_DIR_ROOT/config" 2>/dev/null || true
echo "配置校验："
ssh -G "$HUB_HOST" 2>&1 | grep -E "^(hostname|user|proxycommand|identityfile)" || true

echo "[3/6] 装代理脚本 $PROXY..."
if [ ! -f "$PROXY" ]; then
  mkdir -p "$(dirname "$PROXY")"
  cat > "$PROXY" <<'PYEOF'
#!/usr/bin/env python3
import os, sys, socket, select
import base64
from urllib.parse import urlparse
def main():
    dest_host, dest_port = sys.argv[1], int(sys.argv[2])
    u = urlparse(os.environ.get("HTTPS_PROXY", "http://hatch-egress-proxy:3128"))
    s = socket.create_connection((u.hostname, 3130), timeout=30)
    hdr = ""
    if u.username:
        creds = base64.b64encode(f"{u.username}:{u.password or ''}".encode()).decode()
        hdr = f"Proxy-Authorization: Basic {creds}\r\n"
    s.sendall(f"CONNECT {dest_host}:{dest_port} HTTP/1.1\r\nHost: {dest_host}:{dest_port}\r\n{hdr}\r\n".encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            sys.stderr.write("proxy closed during CONNECT\n"); sys.exit(1)
        buf += chunk
    status = buf.split(b"\r\n", 1)[0]
    if b" 200 " not in status:
        sys.stderr.write(f"CONNECT failed: {status!r}\n"); sys.exit(1)
    rest = buf.split(b"\r\n\r\n", 1)[1]
    if rest:
        sys.stdout.buffer.write(rest); sys.stdout.buffer.flush()
    s.setblocking(False)
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    while True:
        r, _, _ = select.select([s, stdin], [], [], 300)
        if not r: break
        for f in r:
            try: data = os.read(f.fileno(), 65536)
            except OSError: return
            if not data: return
            if f is s: stdout.write(data); stdout.flush()
            else: s.sendall(data)
main()
PYEOF
  chmod +x "$PROXY"
  echo "代理脚本已安装。"
else
  echo "代理脚本已存在，跳过。"
fi
ls -l "$PROXY"

echo "[4/6] 查 Tailscale..."
if command -v tailscale >/dev/null 2>&1; then
  tailscale status 2>&1 | head -3 || true
  if tailscale status 2>&1 | grep -q "Logged out\|Not connected\|stopped"; then
    echo "Tailscale 未连。正在跑 tailscale up（会打印批准链接，须董事长点批准）..."
    tailscale up 2>&1 || true
    echo "若上面给了批准链接，请董事长点批准，批完再往下走。"
  else
    echo "Tailscale 状态见上，若已连则跳过。"
  fi
else
  echo "本机无 tailscale 命令，跳过（若 SSH 不通再排查）。"
fi

echo "[5/6] 测 SSH..."
SSH_OK=0
if ssh -o BatchMode=yes -o ConnectTimeout=15 "$HUB_HOST" true 2>/dev/null; then
  echo "SSH 通了。"
  SSH_OK=1
else
  echo "SSH 暂不通。可能原因：①公钥还没加进枢纽 ②Tailscale 还没批准。"
  echo "先把下面公钥发给董事长加进枢纽，并确认 Tailscale 已批准，再重跑本脚本。"
fi

if [ "$SSH_OK" = "1" ]; then
  echo "[5b/6] SSH 已通，拉取标准工具到本地 ~/workspace/tools/..."
  mkdir -p ~/workspace/tools
  SSH_OPTS="-o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=30"
  for f in kb-ask.sh kb-write.sh kb-rebuild-watch.sh wake-up-check.sh; do
    scp $SSH_OPTS "$HUB_HOST:~/ai-team/scripts/$f" ~/workspace/tools/$f 2>/dev/null && chmod +x ~/workspace/tools/$f && echo "  已拉取 $f" || echo "  拉取 $f 失败（枢纽上可能没有，跳过）"
  done
  echo "[5c/6] 跑唤醒检查..."
  if [ -x ~/workspace/tools/wake-up-check.sh ]; then
    bash ~/workspace/tools/wake-up-check.sh 2>&1 | head -20 || true
  else
    echo "  本地无 wake-up-check.sh，跳过（用枢纽 wake-check-hub.py 直连亦可）。"
  fi
fi

echo "[6/6] 你的 SSH 公钥（发给董事长加进枢纽 ubuntu 的 ~/.ssh/authorized_keys）："
echo "-----BEGIN PUBLIC KEY-----"
cat "$KEY.pub"
echo "-----END PUBLIC KEY-----"
echo ""
echo "董事长须做的两件事：①批准 Tailscale 新设备 ②把上面公钥加进枢纽。"
echo "两件做完，再跑一次本脚本：bash onboard-new-seat.sh"
