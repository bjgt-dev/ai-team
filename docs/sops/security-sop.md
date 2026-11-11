# 安全权限 SOP v1.0（2026-11-10 董事长令，全团队执行）

> 真源：枢纽 `~/ai-team/docs/sops/security-sop.md`；本地 `~/workspace/sops/security-sop.md` 只作镜像。
> 机制：`~/ai-team/scripts/security-gate.py`（门禁脚本，本地镜像 `~/workspace/tools/security-gate.py`）。
> 原则：默认只读、最小权限、执行层校验、不信任模型自己判断；文档不是机制——违反本 SOP 的推进会被 gate 脚本拦截。

## 0. 适用范围

- 本席、光头、Noesis、大管家、总调度、工人、复核席，任何人/任何 agent 经手授权、工具调用、高影响动作，一律走本 SOP。
- 本 SOP 只管授权与权限机制，不产生、不输出、不记录任何真实密钥/token 值；测试一律用占位符。

## 1. 五步 + 撤销演练（顺序不可跳）

| 阶段 | 做什么 | 负责人 | 产出物（缺一不可） | 门禁 |
|---|---|---|---|---|
| 1 register | 身份登记：agent 名、负责人、用途、有效期 | twt / 大管家 | state 内 identities 条目 | 未登记不许授权、不许执行 |
| 2 grant | 签发短期任务级 token + 按工具单独授权（白名单内） | twt / 大管家 | state 内 grants/token 条目 | 越权工具、白名单外工具不许签发 |
| 3 execute | 执行层校验后调用工具 | 执行席 | 审计日志条目（见 §5） | 身份/ token/白名单/审批/审计日志缺一不可 |
| 4 audit | 核对审计日志与执行记录一致 | 复核席 | audit 阶段 done | 日志缺条、记录对不上不许进撤销 |
| 5 revoke / revoke-drill | 停用身份、轮换凭证（演练同此路） | twt / 大管家 | 身份 status=revoked | 撤销后调用必拒，须实测验证 |

- `revoke` 与 `revoke-drill` 二选一为终态：日常收尾用 `revoke`，定期演练用 `revoke-drill`，两者效力相同，演练须额外确认后续调用失败。
- 阶段顺序：register → grant → execute → audit → revoke（或 revoke-drill），缺前置不许推进。

## 2. 一 agent 一身份

- 每个 agent 一个独立身份：agent 名、负责人、用途、有效期四项登记齐全才算登记。
- 不复用人的凭证：人的登录凭证、API Key 不得直接给 agent 用；agent 凭证只属于该 agent。
- 身份过期或被停用后，凭该身份的一切调用必拒；过期身份不许续签，须重新 register。

## 3. 短期任务级 token

- token 只为单个任务签发：绑定 agent、绑定工具（或工具组）、带有效期，用完即收。
- 过期 token 调用必拒；过期 token 不许延期，须重新 grant。
- token 值本身只出现在签发方与执行方的授权通道里，state 文件、日志、聊天里只记 token 占位 ID，不记真实值。

## 4. 工具白名单 + 最小权限

- 默认只读。默认可授工具（只读）：`read_file`、`list_dir`、`search_web`、`read_state`。
- 写/动作类工具须逐个单独授权：`write_file`、`exec_cmd`。
- 高影响工具（见 §6）须逐个授权 + 人工审批单，缺一不可。
- 白名单外工具一律不许签发、不许调用；越权调用在执行层拦截，不依赖模型自己判断。

## 5. 执行层强制校验

- 授权在执行层校验：每一次工具调用前，执行层（gate）依次校验——身份已登记且未过期未停用、token 未过期且属于该 agent、工具在该 agent 的授权白名单内、高影响动作有未消费的审批单、审计日志条目存在。
- 任一校验失败即拦截（exit 1，打印 `VIOLATION`），不执行、不补发、不绕过。
- 不信任模型自己判断：模型说“已授权”“应该可以”不算数，gate 的校验结果说了算。

## 6. 高影响 / 不可逆动作人工批

- 高影响动作包括：`deploy`、`delete_file`、`send_message`、`spend_money`，以及任何不可逆、对外发布、花钱的动作。
- 高影响动作须有人工审批单（approval）：审批单由董事长或大管家签发，`approve` 子命令登记，状态 `unused`。
- 审批单原子消费：一次审批只能用一次；执行成功即标记 `consumed`，重放旧审批单必拒。
- 无审批做高影响动作必拒；审批单过期、属主不符、动作不符一律必拒。

## 7. 审计日志

- 每次授权调用记一条日志：时间、身份（agent）、工具、结果（成功/拦截）。
- 日志写到 agent 写不到的地方：日志路径由签发方指定（如枢纽受控目录），agent 只有追加/只读权限意义上的写入通道由执行层控制，不可由 agent 自行改写；本 SOP 只记路径与格式，不规定 agent 可写路径。
- 日志格式（一行一条，JSON）：`{"ts": "<ISO 时间>", "agent": "<agent>", "tool": "<工具>", "token": "<token 占位 ID>", "result": "ok|denied"}`。
- gate 在 execute 时校验日志条目存在（该 agent + 该工具的条目在日志文件里能找到），缺条不许推进到 audit；audit 阶段逐条核对执行记录与日志。

## 8. 定期演练撤销路径

- 每季度至少一次撤销演练（revoke-drill）：停用一个测试身份、轮换其凭证、随后用旧 token/旧身份发起调用，确认调用失败。
- 演练须留证据：撤销前调用成功、撤销后调用失败（gate exit 1），两次结果都记进审计日志与 state。
- 演练中发现撤销后仍能调用的，按事故处理：停手、报 twt、修好再演练。

## 9. 门禁脚本用法

```
security-gate.py init --job-id <id> --workdir <dir>
security-gate.py advance --state <state.json> --stage register --agent <agent> --owner <负责人> --purpose <用途> --expires-at <ISO 时间>
security-gate.py advance --state <state.json> --stage grant --agent <agent> --tool <工具> --token <token 占位 ID> --expires-at <ISO 时间>
security-gate.py approve --state <state.json> --approval-id <id> --agent <agent> --action <高影响动作>
security-gate.py advance --state <state.json> --stage execute --agent <agent> --tool <工具> --token <token 占位 ID> --audit-log <日志路径> [--approval <审批单 id>] [--high-impact]
security-gate.py advance --state <state.json> --stage audit --agent <agent> --audit-log <日志路径>
security-gate.py advance --state <state.json> --stage revoke --agent <agent>
security-gate.py advance --state <state.json> --stage revoke-drill --agent <agent>
security-gate.py check --state <state.json>              # 全量校验，违规 exit 1
security-gate.py status --state <state.json>              # 阶段表
```

- `grant` 可多次推进（每工具一次）；`execute` 可多次推进（每调用一次）。
- gate 是唯一放行口径；口头“已授权”不算数，state 文件 + 审计日志说了算。

## 10. 违规处理

- gate 拦截即事故信号：停手、报 twt、修状态后再推，不许绕过脚本改 state 文件。
- 同类违规第二次发生，加自动化防护；第三次按系统事故处理。

## 11. 参考

- OWASP AI Agent Security Cheat Sheet；OWASP Top 10 for LLM（LLM01 提示注入、LLM06 过度代理）；OWASP 2026 版 Top 10 for Agentic Apps（ASI01/02/03）；NIST AI RMF；Microsoft 最小权限原则。

## 12. 版本

- v1.0 2026-11-10：市面共识（OWASP/NIST/Microsoft 最小权限）+ 团队五步映射；董事长令固化成机制。
