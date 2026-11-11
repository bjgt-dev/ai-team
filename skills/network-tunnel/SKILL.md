---
name: network-tunnel
description: 网络通道与模型链路：CF 隧道打通 newapi、解决 Tailscale 与代理软件互斥、面板公网化。适用于任何需要公网访问内网服务的场景。
---

# Network Tunnel：网络通道与模型链路

## 1. CF 隧道打通 newapi

### 背景

VPS 的 Tailscale 是 userspace 模式（ocarun 启动），实测不支持自定义端口转发。`http://<VPS_TAILSCALE_IP>:3000` 从外部连超时。

### 解法

模型请求走 Cloudflare 隧道公网域名：

```
https://newapi.noesis.ccwu.cc/v1 → 127.0.0.1:3001 (newapi)
```

Tailscale 只用于 22 端口 SSH/rsync。各行其道。

### 配置要点

```bash
# cloudflared 隧道配置（已有隧道加 ingress）
# 经 CF API 更新：
{
  "ingress": [
    {"hostname": "newapi.noesis.ccwu.cc", "service": "http://127.0.0.1:3001"},
    {"service": "http_status:404"}
  ]
}

# DNS CNAME
# newapi.noesis.ccwu.cc → <TUNNEL_ID>.cfargotunnel.com
```

### 避坑：User-Agent 拦截

经 CF 隧道的请求，Python urllib 默认 UA 会被 403：

```python
# 错误
urllib.request.urlopen(url)  # 403

# 正确
req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
urllib.request.urlopen(req)  # 200
```

**铁律**：所有经 CF 隧道的 Python 请求必须加 UA 头。

## 2. Tailscale 与代理软件互斥

### 问题

Android 单 VPN 槽位限制：Tailscale 和 karing（或其他代理 App）互斥，开一个挤掉另一个。用户手机主力用 karing，导致 `http://<hub-ip>:8090` 打不开。

### 解法 A：面板公网化（推荐）

复用现有 CF 隧道，加一条 ingress：

```
panel.noesis.ccwu.cc → http://127.0.0.1:8090
```

手机浏览器直接打开公网域名，karing 保持开启，零配置。

**操作步骤**：
1. CF API 更新隧道 ingress（加 hostname）
2. 建 DNS CNAME 记录
3. 起 cloudflared 客户端（systemd 服务，开机自启）

```bash
# systemd 服务示例
# /etc/systemd/system/cloudflared-panel.service
[Service]
ExecStart=/usr/local/bin/cloudflared tunnel --no-autoupdate run --token-file /root/panel.token
Restart=always
```

**踩坑**：隧道配置经 API 更新后，本地 cloudflared 不会自动重载。必须重启进程。

### 解法 B：karing 分流（备选）

如果一定要用 Tailscale IP 访问，在 karing/sing-box 配置中加分流：

```json
{
  "route": {
    "rules": [
      {
        "ip_cidr": ["<hub-ip>/10"],
        "outbound": "direct"
      }
    ]
  }
}
```

让 `<hub-ip>/10`（Tailscale CGNAT 段）直连，不走代理。但 Android 上 Tailscale 和 karing 不能同时开，此法仅适用于桌面端。

### 选型建议

| 场景 | 推荐方案 |
|------|----------|
| 手机访问面板 | 解法 A（公网域名） |
| 桌面端已装 Tailscale | Tailscale IP 直连 |
| 桌面端用代理 | 解法 B（分流规则） |

## 3. 模型网关降级链

newapi 中配置 `executor-primary` 的多优先级降级：

```
gpt-oss-120b (ch3, pri 20) → agnes-3.0-flash (ch7, pri 15) → glm-4-flash (ch4, pri 5)
```

**原理**：newapi 重试时会跨优先级档，前一个挂了自动试下一个。免费池 429 时自动降级，保证任务能跑完。

**配置方式**：直改 channels 表的 `model_mapping` 和 `models` 列（API 登录被限流时走 DB 是可行退路，但建行必须逐列对齐）。

## 4. SSH 通道

```bash
# 经 Tailscale SSH（日常运维标准）
ssh -o BatchMode=yes -o StrictHostKeyChecking=no \
  -o ProxyCommand='/home/hatch/.ssh/ts-proxy.sh %h %p' \
  -i /home/hatch/.ssh/id_ed25519 \
  ubuntu@<hub-ip>
```

**注意**：本机经 `hatch-egress-proxy:3130` 出站（不是 3128），ts-proxy.sh 用 socat PROXY CONNECT。
