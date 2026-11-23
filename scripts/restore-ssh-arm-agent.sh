#!/bin/bash
# restore-ssh-arm-agent.sh — 重建 /root/.ssh/config 的 Include 行
# 根因：VM 更换/换账号会清掉 /root/.ssh/config（只含一行 Include，文件在持久家目录之外）；
# 本席以 root 身份运行，ssh 读不到配置就走直连，被运行时转到 egress 代理 3128，3128 按策略掐 SSH，
# 报错形如 `Connection reset by 198.19.0.1 port 3128`。私钥本身在 /home/hatch/.ssh，不受影响。
# 用法：bash ~/workspace/tools/restore-ssh-arm-agent.sh（任意时刻可重跑，幂等）
set -u

mkdir -p /root/.ssh
printf 'Include /home/hatch/.ssh/config\n' > /root/.ssh/config
chmod 600 /root/.ssh/config

echo "--- ssh -G arm-agent (ProxyCommand 必须在列) ---"
ssh -G arm-agent | grep -E '^(proxycommand|hostname|user|identityfile) ' || {
  echo "FAIL: ssh -G 未加载 ProxyCommand，配置仍有问题"; exit 1
}

echo "--- live ssh test ---"
ssh -o BatchMode=yes -o ConnectTimeout=15 arm-agent 'echo SSH-OK' || {
  echo "FAIL: ssh 未通（配置已重建，可能是对端 authorized_keys 或网络问题）"; exit 1
}
echo "OK: SSH 已恢复"
