#!/bin/bash
# restore-ssh-arm-agent.sh -- 重建 /root/.ssh/config 的 Include 行（VM 更换会清掉它）
# 症状：ssh arm-agent 报 Connection reset by 198.19.0.1 port 3128（流量走直连被运行时转到 egress 代理 3128，3128 掐 SSH）
# 根因：本席以 root 身份运行，ssh 读 /root/.ssh/config；VM 一换该文件就没了，ProxyCommand 不加载
# 用法：bash ~/workspace/tools/restore-ssh-arm-agent.sh（然后 ssh -G arm-agent 验 ProxyCommand 在不在）
set -u
mkdir -p /root/.ssh
echo "Include /home/hatch/.ssh/config" > /root/.ssh/config
chmod 600 /root/.ssh/config
echo "OK：/root/.ssh/config 已重建"
ssh -G arm-agent 2>/dev/null | grep -i "proxycommand" || { echo "FAIL：ProxyCommand 仍未加载"; exit 1; }
echo "OK：ProxyCommand 已加载"
