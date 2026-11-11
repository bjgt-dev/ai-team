# AI 团队系统状态（VPS 控制面）

> 更新时间：2026-10-20 03:30 CST | 维护者：Noesis（IT 主管）
> 新号接管时先读此文件，再读 MEMORY.md 关键段。

## 核心架构

- **VPS** (<vps-ip>)：OpenClaw 网关 + newapi + verify.py 门禁 + 8090 面板
- **执行面**：Cue VM（目前断联），备用：本地临时执行
- **面板**：https://example.com（公网，CF 隧道）
- **模型网关**：https://example.com/v1（免费池）

## 关键路径

- 契约：`/home/ubuntu/ai-team/contracts/pending/cue/`
- 证据：`/home/ubuntu/ai-team/inbox/`
- 知识库：`/home/ubuntu/ai-team/knowledge/`
- 隔离区：`/home/ubuntu/ai-team/quarantine/`
- 脚本：`/home/ubuntu/ai-team/scripts/`（verify.py, ingest.sh, scan_inbox.sh, review_gate.py）
- Skills：`/home/ubuntu/ai-team/skills/{vps-gatekeeper,cloud-worker,network-tunnel}/`
- 方法论：`/home/ubuntu/ai-team/knowledge/methodology-001.md`

## 常驻服务

- scan_inbox.sh：系统 cron 每分钟（自动验收）
- dispatcher-pump：OpenClaw cron 每分钟（STALLED 故障切换）
- cloudflared-panel：systemd（面板公网隧道）
- 面板进程：`/opt/openclaw/app.py`（8090，无认证⚠️待加）

## 待办

- [ ] 面板加 Basic Auth（公网裸奔中）
- [ ] review_gate.py 的 newapi token 未配（API 限流）
- [ ] Cue 断联（02:27 后无拉取），待恢复
- [ ] 旧 Hermes gateway 进程未杀（PID 2721731，占 344MB）

## SSH

```
ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o ProxyCommand='/home/hatch/.ssh/ts-proxy.sh %h %p' -i /home/hatch/.ssh/id_ed25519 ubuntu@<vps-ip>
```
