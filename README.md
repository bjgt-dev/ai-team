# ai-team

多智能体自动化团队：一套用中央队列 + 租约 + 门禁驱动的 agent 协作系统。
A multi-agent automation team: a central task queue with leases, a policy
gate service, an evaluation harness, a skill library, and the SOPs that hold
it together.

> 状态：首版公开仓库由内部 `~/ai-team` 脱敏导出（见
> [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) 与审计说明）。
> 凭据、私人数据、儿童相关内容、业务情报、运行日志一律不在此仓。

## 它是什么

- **中央队列**：任务以合同（contract）形式进入中央队列，工人按租约认领，
  租约过期重派，幂等执行，失败隔离（quarantine），不是“发消息等人回”。
- **门禁（gate-svc）**：所有高风险动作（部署、写生产库、git push、systemctl、
  ssh 到各节点、数据库迁移）走门禁校验：先查知识库与记忆拿回执，再校验
  签名，通过才执行；操作脚本不存在时默认拒绝（fail-closed）。
- **评测（eval OS）**：任务执行前后过 verifier 五门；三考试（正常成功 /
  篡改拦截 / 空工作目录）是评测方法的回归用例。
- **技能库（skills/）**：58 个技能包（团队自研 + Matt Pocock 开源技能），
  覆盖派发、执行、复核、情报、内容、运维。
- **SOP 与架构文档（docs/）**：交接、预检、派发、复盘、评审的标准流程。

## 目录结构

```
scripts/            # 核心脚本：门禁、队列 API、执行器、验证器、巡检、监视
  gate-svc.py       # 门禁服务（标准库 http.server，见下文“部署”）
  hubctl            # 门禁客户端（bash + curl）
  queue_api-*.py    # 中央队列 API
  c2-executor-*     # 执行器
  c2-verifier-*     # 验证器
  team-monitor.sh   # 团队监视探针（只读）
docs/               # 架构、工程规范、SOP
skills/             # 技能定义（58 包，见 THIRD_PARTY_NOTICES.md）
contracts/          # 任务合同 schema（CONTRACT_SCHEMA.md）
state/l3-v2/        # 角色注册表与权限设计
state/handovers/    # 席位交接模板
state/eval-os-phase2/  # 三考试方法与 wire log
state/baselines/    # 版本基线示范
services/advisor/ services/channel/  # 小服务
configs/ prompts/   # 路由配置、执行提示词
```

不在此仓的东西（另行决策，不随首版公开）：
`services/ds2api/`（GPLv3）、`muse2api/`、儿童陪伴项目、知识库、运行日志、
队列数据、数据库备份、全部凭据。

## 快速开始

```bash
git clone https://github.com/bjgt-dev/ai-team.git
cd ai-team
# 1. 门禁服务（默认只监听本机；生产上用 Tailscale 等内网暴露）
GATE_BIND_HOST=127.0.0.1 GATE_BIND_PORT=8093 python3 scripts/gate-svc.py
# 2. 客户端
export GATE_SVC_URL=http://127.0.0.1:8093
./scripts/hubctl search <seat> <slug> <query>
```

## 部署门禁（必读）

1. **自建两个密钥文件**（仓库里没有，部署时自己生成，绝不要提交）：
   - `gate.key`：回执 HMAC 签名密钥。生成：`python3 -c "import secrets; print(secrets.token_hex(32))"`，
     放到只有 gate-svc 能读的位置（示例：`/etc/noesis/gate.key`，权限 600），
     用环境变量 `GATE_KEY_FILE` 告诉 gate-svc。
   - `chairman.token`：董事长批准令牌，`/approve` 接口校验它。
     生成与存放同上（`GATE_CHAIRMAN_TOKEN_FILE`）。
   - 轮换：换文件后重启 gate-svc；旧回执按行内保存的签名校验，不受影响。
2. **`/act` 的操作脚本需自行实现**：门禁只校验、不替你执行。
   `ACTION_WHITELIST` 里的 6 个操作（deploy、写生产库、git push、systemctl、
   ssh 到各节点、数据库迁移）各自对应一个操作脚本；脚本不存在时 enforce
   模式下**全部拒绝**（fail-closed）。先实现脚本、跑通验收，再开 enforce。
3. **不要把 gate-svc 暴露到公网**：`/search`、`/gate`、`/act` 只有签名校验，
   没有限流与来源校验。只绑内网地址（`GATE_BIND_HOST`，默认 `127.0.0.1`），
   用 Tailscale / WireGuard / 内网访问。
4. **数据库**：回执与任务库走 PostgreSQL；连接串用环境变量或外部凭据文件
   传入，不要写进仓库（代码里只引用凭据文件路径，不含值）。

## 已知问题（首版发布前合入修复，不要隐瞒）

- **回执同源缺陷**（`scripts/gate-svc.py` `_handle_search`）：一次 grep 产生
  一个 `result_hash`，`memory` 与 `kb` 两行回执除 `kind` 外完全相同——
  所谓 memory 回执并不证明查过 memory。已修复（2026-11-11）：memory 回执单独 grep 席位记忆文件、各算各的 `result_hash`，并经探针验收（两 kind 的 `result_hash` 不同，旧回执仍可校验）。
- **SQL 拼接**：回执入库用字符串拼接 + 转义，未参数化；部署前建议审计
  注入面或改参数化查询。

## 贡献

见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。核心规则：不提交任何真实凭据、
真实姓名、内网地址、邮箱；改动先跑 `python3 -m py_compile`、
`bash -n` 与 `python3 scripts/dename-lint.py --strict`（脱名检查，
真实姓名由环境变量 `CHAIRMAN_REAL_NAME` 指定）。

## 许可证

MIT，见 [`LICENSE`](LICENSE)。第三方技能来源见
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
