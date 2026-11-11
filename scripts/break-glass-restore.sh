#!/bin/bash
# break-glass-restore.sh — 把破玻璃封存的凭证还给原账户（仅 rollback-all.sh 调用）
# 从 /etc/noesis/break-glass/（root:chairman 0400）复制回原位置。用 sudo 读，不打印任何值。
# 幂等：封存缺失时报错退出，不覆盖不存在的原文件路径则跳过。
set -u
BG=/etc/noesis/break-glass
HOME_DIR=/home/ubuntu
AI_TEAM=$HOME_DIR/ai-team
sudo test -d "$BG" || { echo "break-glass-restore: $BG missing" >&2; exit 1; }
restore_one() { # $1=封存名 $2=目标路径 $3=权限
  src="$BG/$1"; dst="$2"; mode="$3"
  if sudo test -f "$src"; then
    sudo cp "$src" "$dst"
    sudo chmod "$mode" "$dst"
    sudo chown ubuntu:ubuntu "$dst"
    echo "restored $2"
  else
    echo "skip $2 (no sealed copy)" >&2
  fi
}
restore_one ssh-id_ed25519 "$HOME_DIR/.ssh/id_ed25519" 600
restore_one ssh-authorized_keys "$HOME_DIR/.ssh/authorized_keys" 600
restore_one cq-dsn "$AI_TEAM/state/.cq-dsn" 600
restore_one newapi-token "$AI_TEAM/state/.newapi-token" 600
for f in b2-hub-backup box-simular-api-key brain-proxy cloady-api-token hf-temp-20261109 modelscope-api-token muse-meta-temp-20261108 newapi-admin; do
  restore_one "$f.env" "$AI_TEAM/state/$f.env" 600
done
if sudo test -d "$BG/gh-config"; then
  sudo cp -r "$BG/gh-config/." "$HOME_DIR/.config/gh/"
  sudo chmod 700 "$HOME_DIR/.config/gh"
  sudo chown -R ubuntu:ubuntu "$HOME_DIR/.config/gh"
  echo "restored $HOME_DIR/.config/gh/"
fi
echo "break-glass-restore: done"
