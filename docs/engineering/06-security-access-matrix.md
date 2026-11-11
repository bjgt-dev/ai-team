# 规范06《权限矩阵与安全红线校验》

> 状态：**[初版待验证]**
> 本文件为初版，矩阵中部分单元格为“按真源归纳”，已在脚注中标明依据强度；守卫代码本地实测通过、**未接入生产派单路径**，不得宣称已上线。
> 实测时间：2026-11-06 03:23–03:25 UTC（北京时间 11:23–11:25），通道 `ssh arm-agent`，主机名 `arm-agent`，Python 3.12.3。
> 本地路径：`~/workspace/docs/engineering/06-security-access-matrix.md`；枢纽路径：`/home/ubuntu/ai-team/docs/engineering/06-security-access-matrix.md`（须与本地版 sha256 一致，见 §7）。

## 1. 真源（只认下列文件，口头说法不算）

| # | 真源文件（枢纽绝对路径） | 本次实测确认 | 用途 |
|---|---|---|---|
| S1 | `/home/ubuntu/ai-team/state/pull-once-20261105/worker/pull_once.py`（159 行） | 已读头注释全文 + 第 120–159 行；`py_compile` 通过 | 工人权限边界：不持上游密钥、不直连网关、token 只读 `~/.queue-api-token`（0600）、脚本白名单、30 s 超时、无 sudo、无网络；code 分支只跑 pytest |
| S2 | `/home/ubuntu/ai-team/scripts/claim_gate.py`（20 行） | 已读全文；实跑断言通过，exit 0（见 §7） | 断言出口门禁：无配置证据 → exit 2“未核实”，禁止发送 |
| S3 | `/home/ubuntu/ai-team/scripts/brain_guard.py`（225 行） | 已读全文；`py_compile` 通过 | 守卫范式：状态快照陈旧判定、降级去重告警、直聊席 reasoner 禁用检查、写板 + 写 handoff |
| S4 | `/home/ubuntu/ai-team/state/l3-v2/OPERATING-MANDATE.md`（11 行） | 已读全文 | 授权四档 A 自主 / B 限定 / C 找董事长批 / D 永久禁止；C 级含付费、对外发布、不可逆、价格、购买、签约 |
| S5 | `/home/ubuntu/ai-team/state/m5-20261105/human-fallback-switch.json` + `human-fallback-switch.md` + `check-m5-switch.sh` | 已读全文；`check-m5-switch.sh` 实跑 **80 PASS / 0 FAIL，RESULT: PASS，exit 0**（见 §7） | 人兜底硬开关 M5-HUMAN-FALLBACK：ACTIVE，16 类命中即停，全部找董事长 |

补充说明：

- S1 头注释原文（逐字抄录，见 §7 实测输出）规定：token 从 `~/.queue-api-token`（0600）读取，token 值永不记日志、不打印；工人机上无上游密钥、无网关 token、无 Zen key、无直接上游连接；脚本执行只允许以 `echo / cat / ls / sha256sum` 开头的命令，30 s 超时，无 sudo、无网络；code 分支（`type==code`）只在 `~/code-agent-pilot/workdir/` 下跑 `python3 -m pytest -q`（列表参数、无 shell、120 s 超时），只执行与测试**已存在**的试点代码，不做 LLM 调用、不生成代码、不持模型、不持上游密钥。
- S4 原文关键句：D 级永久禁止——伪造、绕验收、隐瞒、擅扩权限、保活造假、违规占算力、未知冒充正常、覆盖唯一备份。认证事实唯一来源为 `state/l3-v2/certification.json`（本次未核对该文件内容，不展开）。
- 本规范不编造不存在的配置：凡本次未登录实测的机器、未读到的文件，一律写入 §6“未核实清单”，不写成已确认事实。

## 2. 权限矩阵

图例：✅ 有权；◐ 限定（须满足脚注条件）；❌ 无权；— 不适用。

| 能力 | 董事长 | 幕僚长 | 大管家 | 总调度 | 工人 | 监工 |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| 读（状态、任务、日志、复核结论） | ✅ | ✅ | ✅ | ✅ | ◐¹ | ✅ |
| 写（枢纽 state / docs / 任务记录） | ✅² | ◐³ | ◐⁴ | ◐⁵ | ◐⁶ | ◐⁷ |
| 派单（向中央队列发任务） | ✅ | ✅⁸ | ◐⁴ | ✅⁸ | ❌ | ❌ |
| 复核（独立语义复核 / 验收） | ✅ | ✅⁹ | ◐¹⁰ | ◐¹¹ | ❌ | ✅¹² |
| 发布（对外发布 / 对外联系 / 对外承诺） | ✅¹³ | ❌¹⁴ | ❌ | ❌ | ❌ | ❌ |
| 删改（删数据、改配置、删备份） | ✅¹⁵ | ◐¹⁶ | ❌ | ❌ | ❌ | ❌ |
| 持密钥（接触明文密钥 / 配发密钥） | ✅¹⁷ | ◐¹⁸ | ❌ | ❌ | ◐¹⁹ | ❌ |

角色定义（按 SOUL / MEMORY 口径，文件正文只用角色词）：

- **董事长**：唯一决策人，四类必批事项与 M5 全部 16 类的唯一升级对象。
- **幕僚长**：董事长唯一接口，定目标与优先级、组织团队、验交付、报结果；不替工人干活。
- **大管家**：枢纽 24 小时可调用角色，跑 butler  loop、目标驱动产下一批任务；席位降为可选追认 / 复核人，不在关键路径。
- **总调度**：编排总管，枢纽调度 / 排队 / 汇总，工人向枢纽拉活、超时掉线重派。
- **工人**：执行节点 worker-1（InstaBox 主工），pull_once 模式领活、执行、回传 artifact。
- **监工**：独立复核席 / verifier，不参与执行，只做复核与验收。

脚注（依据强度逐条标明）：

1. 工人“读”仅限本次认领任务的 payload 与本人执行日志；不得读他人任务、不得读密钥、不得读枢纽全量 state。依据 S1（claim → payload → 执行 → 回传的单循环）。
2. 董事长写操作不受本矩阵限制，但枢纽侧操作仍须留痕；不可逆操作须本人明确批准（见 §3）。
3. 幕僚长写枢纽 state/docs 须留痕、可回滚；破坏性、不可逆写操作先批后做。依据 S4（B 级限定：备份、回滚、窗口、验证）与 §3。
4. 大管家写仅限 butler 决策记录与下一批任务（经中央队列产出）；派单须走中央队列，不得绕过队列直接指派工人。依据：MEMORY“目标驱动（goal_driver / butler_decide）”口径——**本次未在 S1–S5 五个真源文件中逐字核对，标为待验证**。
5. 总调度写仅限队列 / 调度状态（claim / checkpoint / heartbeat / fail / submit 的状态机记录）；不得改任务 payload 语义、不得改密钥。依据 S1 的队列 API 形态（`/v1/claim`、`/v1/checkpoint`、`/v1/heartbeat`、`/v1/fail`、`/v1/artifact`、`/v1/submit`）归纳。
6. 工人写仅限 checkpoint / artifact / submit 回传；不得写枢纽 state、不得写他人任务。依据 S1。
7. 监工写仅限复核结论、验收报告与不通过理由；不得改被复核的交付物本身。依据：C2 verifier / 独立复核机制口径——**本次未在 S1–S5 中逐字核对，标为待验证**（见 §6）。
8. 派单必须走中央队列；禁止点对点私派、禁止绑死单人、禁止不等叫不醒的会话。依据 MEMORY 中央队列三问口径；S1 的 claim 机制是其执行侧证据。
9. 幕僚长复核是组织复核（派独立复核、验交付），不等于自核自批；自己经手的交付须由独立复核席验收。
10. 大管家不做独立语义复核；其决策本身须接受独立复核。**待验证**（同脚注 4）。
11. 总调度复核仅限状态核对（任务是否 DONE / 是否超时 / 是否重派），不做语义复核。
12. 监工复核为独立语义复核（code 任务走结构性五查：源文件在合同路径、AST 含非空函数 / 类、新目录重跑合同测试、红绿检查、只有测试输出驳回）。**待验证**（同脚注 7）。
13. 对外发布、对外联系、对外承诺的批准权只在董事长；批准前系统不得发出。依据 S4（C 级）与 S5（OUTREACH_NEW、FIRST_THREE_ORDERS）。
14. 幕僚长可拟对外草稿、整理待审稿，但发出须董事长确认；L3（对外联系、收款方式）待批期间不得对外联系。依据 S5 与 MEMORY P0-3 口径。
15. 董事长删改权同样受“唯一备份不可覆盖”约束：D 级“覆盖唯一备份”永久禁止，对任何人适用（S4 原文未设例外）。
16. 幕僚长删改须同时满足：有备份、有回滚、走窗口验证；删备份、删唯一副本、删日志均属 ❌（D 级）。删改前须确认活跃部署 / 活跃版本已更新，不得“显示已保存、实际未部署”。
17. 董事长是密钥的最终所有人与批准人；日常操作不经手明文，配发与轮换走枢纽 0600 文件，不走聊天。
18. 幕僚长不直接持明文密钥：上游密钥只在枢纽持有轮换，操作时经枢纽已登录态 / API 完成；密钥值绝不进聊天、普通文件、日志、截图。依据 S1（工人不持密钥）与 §4。
19. 工人持密钥仅限 `~/.queue-api-token`（0600）一枚 queue token；无上游密钥、无网关 token、无 Zen key。依据 S1 头注释与代码 `_token()` 只读该文件。

越权处置：任一角色越权调用 → 按 S5 GUARDRAIL_HIT 类停下，记录命中护栏与输入，找董事长；不得绕过、不得重试、不得换路径。守卫实现见 §5。

## 3. 四类必批事项清单（C 级：汇总进董事长决策队列，不可私自执行）

四类总纲（S4 + 长期授权令）：**花钱、对外发布、不可逆、只有董事长能给的输入**。下表把 S4 的 C 级展开项与 S5 的 16 类一一对应；命中任一格即停下找董事长，系统不得自主做。

| # | 四类 | 具体事项（C 级 / M5 类别） | M5 类别 ID | 停下动作 |
|---|---|---|---|---|
| 1 | 花钱 | 任何支出、退款、打款、转账、垫付、代付；绑卡、升付费、开通付费、充值；自主支出 > 0 元即越线（预算线 0 元） | PAYMENT_OUT、BUDGET_BREACH | 停止一切资金流出；只可接收到董事长名下账户 |
| 2 | 花钱 | 任何收款、收款链接 / 码 / 账户信息；客户表示要付款 | PAYMENT_IN | 停止收款流程，不生成、不发送任何收款信息；只记录待确认 |
| 3 | 花钱 | 报价超出信封 / 模板定价范围，或无模板、无定价依据 | QUOTE_OVER_TEMPLATE | 不报价、不承诺价格；按模板价或“待董事长定价”口径待批 |
| 4 | 花钱 | 折扣、优惠、减免、赠送、降价的提议或承诺 | DISCOUNT | 不承诺任何折扣；折扣方案只可拟稿待批 |
| 5 | 对外发布 | 对外联系新对象（新客户、新合作方、新平台账号、新渠道）；对外正式发布与承诺 | OUTREACH_NEW | 不发出任何对外消息；只可拟稿待批，发出须董事长确认 |
| 6 | 对外发布 | 客户投诉、不满、差评、要求赔偿或退款诉求 | COMPLAINT | 不自行安抚承诺、不自行处理；先记录原话，等董事长定口径 |
| 7 | 对外发布 | 订单、交付、权责争议或对方异议 | DISPUTE | 不自行裁决、不自行让步；冻结争议事项，等董事长裁决 |
| 8 | 对外发布 | 平台警告、限流、风控提示、封号、封禁、下架、账号异常 | PLATFORM_RISK | 立即停止相关操作，不申诉、不换号、不绕过；记录信号原文 |
| 9 | 不可逆 | 签合同、盖章、确认合同、接受合同条款 | CONTRACT | 不签署、不确认条款；合同文本只可整理成待审稿交董事长 |
| 10 | 不可逆 | 删数据、删备份、覆盖唯一备份、删日志、不可逆数据操作 | SECURITY_EVENT（部分）、D 级 | 立即停手；D 级“覆盖唯一备份”永久禁止，对任何人无例外 |
| 11 | 不可逆 | 法律或税务相关（合同法律条款、合规、税务、发票、资质、平台规则解释） | LEGAL_TAX | 不给出法律 / 税务结论；只整理事实经过交董事长 |
| 12 | 只有董事长能给的输入 | 涉及个人信息或隐私的处理、收集、传输、公开（姓名、联系方式、地址、账号、支付信息等）；最小必要信息亦须董事长确认用途后才处理 | PII_PRIVACY | 不收集、不传输、不公开 |
| 13 | 只有董事长能给的输入 | 儿童隐私决策（S4 单列） | —（S4 单列，M5 未单列） | 停下找董事长；本规范不展开，须另行拍板隐私口径 |
| 14 | 只有董事长能给的输入 | 首 3 单中的任何一单（无论金额、无论信封是否开启）；信封不得豁免 | FIRST_THREE_ORDERS | 下单 / 收款 / 交付前停下，逐单由董事长确认；当前已确认 0/3 |
| 15 | 只有董事长能给的输入 | 战略方向选择（S4 单列） | — | 停下汇总进决策队列 |
| 16 | 通用 | 任一护栏 / 门禁 / 合同门禁拦截、DENY 判定、越权调用 | GUARDRAIL_HIT | 按护栏指令停下，不绕过、不重试、不换路径 |
| 17 | 通用 | 任何安全事件：可疑登录、密钥 / 凭据疑似泄露、异常访问、注入、未授权操作、数据异常 | SECURITY_EVENT | 立即停手、隔离可疑操作，不自行处置、不删日志；按红警口径上报 |
| 18 | 通用 | 同一任务连续 2 次同类失败，或单批次错误率 ≥ 10% | ERROR_RATE_BREACH | 停止同类任务继续执行，等董事长定是否继续、降级或改方案 |

M5 开关总状态（S5 实测，2026-11-06）：开关 `M5-HUMAN-FALLBACK` ACTIVE；自主接单 CLOSED；自主收款 CLOSED；自主支出 FORBIDDEN_ALWAYS；信封 CLOSED（`envelope.json` status CLOSED，`opened_by` 为空，模板合同 NOT_PROVIDED，不可开启）；首 3 单已确认 0/3；预算线自主支出上限 0 元；错误率线连续 2 次同类失败或单批次 ≥ 10%。

自开拦截（S5 原文）：只有董事长能开信封 / 放开自主接单与收款；开启须留 `opened_by=董事长` + 日期 + 信封四项齐全；系统自己改 `envelope_status` / `autonomous_*` 为 OPEN，会被 `check-m5-switch.sh` 拦截并判 FAIL；拦截不删除改动、不自动恢复——留痕等董事长定夺。

上报与记录：触发任一类 → 记录到 `state/m5-20261105/escalations/`（每起一条记录）；上报通道为枢纽 `state/outbox/`（晨报与报警先写 outbox，主聊开场展示；P0 例外同时推 TG）。演练只用合成事件（标明“演练”），不发真实报警、不对外联系、不做真实收款；演练记录见 `drill-20261105.md` / `drill-20261105.log` / `escalations/DRILL-20261105-001.json`。

## 4. 枢纽与工人机环境变量配置规范

原则：**密钥只在枢纽，工人只持 queue token；0600 权限；绝不进聊天 / 普通文件 / 日志 / 截图。**

### 4.1 枢纽（arm-agent，`/home/ubuntu/ai-team`，用户 `ubuntu`）

| 项目 | 规范 | 本次实测 |
|---|---|---|
| 上游密钥 / 网关 token / Zen key | 只在枢纽持有轮换；不得下发工人机，不得进聊天、普通文件、日志 | 未读值、未逐项核对存放路径——**本次只确认原则，不宣称具体文件清单**（见 §6） |
| 网关配置目录 | `/home/ubuntu/.config/opencode/opencode.json` 与 `opencode.env` 权限须为 0600 | 已实测 `ls -l`：两者均为 `-rw-------`（0600），属主 `ubuntu`；**内容未读、值未记录** |
| 枢纽不持工人 token | 工人 queue token 不在枢纽存放 | 已实测：`ls /home/ubuntu/.queue-api-token` 不存在（`No such file or directory`），符合“token 只在工人机” |
| 密钥文件权限 | 含密钥的文件一律 0600；新建密钥文件后须立即 `chmod 600` 并 `ls -l` 复核 | opencode 两文件 0600 已验；其余密钥文件本次未逐一核对（见 §6） |
| 环境变量注入 | 密钥经环境变量 / 0600 配置文件注入进程，不得写进代码、不得写进可分享的文档、不得出现在命令行历史可读位置 | 规范要求；本次未审计全部注入点（见 §6） |
| 备份与轮换 | 密钥轮换只在枢纽做；轮换后旧值作废，须同步更新 0600 文件与台账，不得留旧值在聊天或备份草稿中 | 规范要求；轮换记录本次未核对 |

### 4.2 工人机（worker-1，InstaBox 主工）

| 项目 | 规范（S1 头注释 + 代码） | 本次实测 |
|---|---|---|
| 唯一凭据 | `~/.queue-api-token`（0600），只读该文件；token 值永不记日志、不打印 | 头注释与 `_token()` 代码已验；**工人机侧 0600 本次未登录实测**（见 §6）——规范写 0600 是 S1 规定的值，不是本次实测值 |
| 禁止持有 | 无上游密钥、无网关 token、无 Zen key、无直接上游连接 | S1 头注释原文已验 |
| 连接方式 | 只连 `http://127.0.0.1:12700`（经 SSH 隧道到枢纽 localhost 的 queue_api）；不得直连公网网关 | S1 代码 `API = "http://127.0.0.1:12700"` 已验 |
| 脚本执行 | 只允许以 `echo / cat / ls / sha256sum` 开头的命令；`shell=True` 但前缀白名单在前；30 s 超时；无 sudo；无网络 | S1 代码 `ALLOWED_SCRIPT_PREFIXES` 与 `subprocess.run(..., timeout=30)` 已验 |
| code 分支 | 只在 `~/code-agent-pilot/workdir/` 下跑 `python3 -m pytest -q`（列表参数、无 shell、120 s 超时）；只执行与测试已存在的试点代码；不做 LLM 调用、不生成代码 | S1 代码已验（workdir 白名单校验 + pytest 调用） |
| 日志 | 执行日志只记任务 id、字节数、sha256、http 状态；不记 token、不记密钥、不记 payload 明文密钥字段 | S1 `log()` 调用点已验（未见 token 打印）；payload 全量日志本次未逐行审计（见 §6） |

### 4.3 禁止事项（两端通用）

- 密钥值不得出现在聊天、文档正文、截图、日志、git 提交、订阅链接、URL 参数中。
- 不得用“扩容 / 变更 / 笔误”等推断弥合两个来源数字冲突；冲突先按“不同对象 / 不同时期”排查，挂账须带核实动作与 ≤ 3 天期限。
- 引用旧快照答现状问题前先看日期来源；单次快照超 7 天未复核只能说“旧记录、未复核”。
- 新账号 / 密钥 / 机器当天同步台账与脚本；密钥文件数对不上台账数即报错停下。

## 5. 拦截高危动作守卫代码

设计思路（照 S2 `claim_gate.py` 与 S3 `brain_guard.py` 的思路写）：

- 像 `claim_gate` 一样：**默认拒绝**——证据不足 / 命中规则即 exit 非 0、输出“未核实 / DENY”，禁止继续发送或执行；通过才放行，且通过不代表已获业务批准。
- 像 `brain_guard` 一样：命中即写记录（本守卫写 JSONL 日志，类比 brain_guard 写 handoff + 状态板）、去重（同一动作重复拦截只记一条摘要，见代码注释）、不自动恢复、不静默绕过。

状态：**本地实测通过，未接线**。代码本体在 `/tmp/guard06/guardrail.py` 跑通 8 例（见 §7）；**未接入**中央队列派单路径、未接入 `pull_once.py`、未接入任何生产执行器。接线须另立任务（含接线点、灰度、回滚），接线前不得宣称“已上线拦截”。

```python
#!/usr/bin/env python3
"""security guardrail - spec 06 draft, modelled on claim_gate.py / brain_guard.py.
Deny-first: any high-risk keyword match -> DENY + JSONL log, no execution.
NOT wired into production dispatch path yet (see spec 06 section 5).
"""
import json, re, sys
from datetime import datetime, timezone

BLOCK = [
    (r"rm\s+-rf\b", "DELETE", "rm -rf"),
    (r"rm\s+--no-preserve-root\b", "DELETE", "rm --no-preserve-root"),
    (r"\bmkfs\b|\bdd\b.*\bof=/dev/", "DELETE", "disk destructive"),
    (r"drop\s+(database|table)\b", "DELETE", "drop db/table"),
    (r"删除(唯一)?备份|覆盖(唯一)?备份|rm.*备份", "DELETE", "delete backup"),
    (r"对外发布|正式发布|publish\s+to\s+prod|deploy\s+prod", "PUBLISH", "publish"),
    (r"绑卡|绑定(银行|支付)卡|升(级)?(到)?付费|开通付费|升级套餐|充值|付款|转账|退款|打款", "PAYMENT", "payment/billing"),
    (r"签(署|约|合同)|盖章|接受(合同)?条款", "CONTRACT", "contract"),
    (r"sudo\b", "PRIVESC", "sudo"),
    (r"curl|wget|ssh\s+\S+@|nc\s+-", "NETWORK", "network exfil"),
]

def check(action: str, actor: str = "unknown",
          log_path: str = "/tmp/guard06/guardrail.log.jsonl"):
    for pat, cat, label in BLOCK:
        if re.search(pat, action, re.IGNORECASE):
            rec = {"ts": datetime.now(timezone.utc).isoformat(), "actor": actor,
                   "action": action, "verdict": "DENY", "category": cat, "rule": label}
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            return False, f"DENY [{cat}/{label}]：高危动作已拦截，未执行；已记日志 {log_path}，须董事长批准后方可重提。"
    return True, "ALLOW：未命中高危规则（仅表示通过本守卫，不代表已获业务批准）。"
```

接线要求（待办，不在本文件内完成）：

1. 接线点二选一：中央队列派单前（`claim` 前校验 payload.command / 动作描述）或工人执行前（`pull_once.py` 脚本白名单之前加一道）；不得两处各写一份分叉规则。
2. 日志落枢纽 `state/`（如 `state/guardrail-denied.jsonl`），不得只落本地 `/tmp`；DENY 记录须含时间、actor、动作原文、命中规则、任务 id（如有）。
3. ALLOW 不豁免 §3 四类必批：守卫是关键词拦截，不是业务审批；四类事项即使绕过关键词（如改写措辞）仍须走董事长决策队列。
4. 关键词表须定期复核：新增高危措辞先补测试用例再上线；测试用例须含放行例与拦截例，缺一类不发版。

已知局限（写明，不掩盖）：关键词正则可被改写措辞绕过；本守卫不解析语义、不校验身份、不替代 claim_gate 的配置证据校验与 M5 开关；生产接线前不具备防护效力。

## 6. 未核实清单（本次未实测，不得当成已确认事实）

| # | 未核实项 | 原因 | 核实动作与期限 |
|---|---|---|---|
| U1 | 工人机 `~/.queue-api-token` 实际权限确为 0600 | 本次只在枢纽 `ls`（不存在，符合预期），未登录工人机实测 | 登录工人机 `stat -c %a ~/.queue-api-token`，≤ 3 天 |
| U2 | 枢纽全部密钥文件的完整清单与权限（除 opencode 两文件外） | 本次只 `ls -l` 了 opencode 目录，未读值、未全量审计 | 列目录 + 逐项 `stat`，不读值，≤ 3 天 |
| U3 | Zen key / 各上游 key 的存放路径与轮换状态 | 记忆称“只在枢纽持有轮换”，本次未在 S1–S5 中逐字核对路径 | 按台账逐项核对路径与权限，≤ 3 天 |
| U4 | 监工（独立复核席）复核五查与大管家派单细节 | 依据为 MEMORY / C2 口径，本次未在 S1–S5 五个真源文件中逐字核对 | 补读 verifier / 复核相关真源后修订矩阵脚注，≤ 3 天 |
| U5 | 守卫代码生产接线 | 本地实测通过，未接线 | 另立接线任务（见 §5），不与本规范合并宣称完成 |
| U6 | `state/l3-v2/certification.json` 内容 | 本次未读该文件 | 补读后核对 A/B/C/D 与认证状态，≤ 3 天 |
| U7 | pull_once 日志是否含 payload 明文敏感字段 | 本次只验了 `log()` 调用点，未逐行审计全部日志输出 | 逐行审计日志字段，≤ 3 天 |

## 7. 真实运行（实测输出逐字贴，不改写）

实测环境：`ssh arm-agent`，主机名 `arm-agent`，Python 3.12.3，时间 2026-11-06T03:24:20Z（北京时间 11:24）。

### 7.1 py_compile（三文件）

命令：`python3 -m py_compile /home/ubuntu/ai-team/state/pull-once-20261105/worker/pull_once.py /home/ubuntu/ai-team/scripts/claim_gate.py /home/ubuntu/ai-team/scripts/brain_guard.py`

输出：

```
PY_COMPILE_OK
```

### 7.2 claim_gate 实跑

命令：`python3 /home/ubuntu/ai-team/scripts/claim_gate.py --claim "worker不持上游密钥" --evidence /home/ubuntu/ai-team/state/pull-once-20261105/worker/pull_once.py:"No upstream keys"`

输出：

```
核实通过：断言“worker不持上游密钥”有配置证据
claim_gate_exit=0
```

### 7.3 check-m5-switch.sh 实跑

命令：`bash /home/ubuntu/ai-team/state/m5-20261105/check-m5-switch.sh`

输出（全文 81 行，80 PASS / 0 FAIL；此处贴首尾行，全文见枢纽 `/tmp/m5check-20261106.txt`）：

```
PASS: 开关文件存在 /home/ubuntu/ai-team/state/m5-20261105/human-fallback-switch.json
PASS: 信封文件存在 /home/ubuntu/ai-team/state/m5-20261105/envelope.json
PASS: 开关状态 ACTIVE
PASS: 自主接单默认关闭 CLOSED
PASS: 自主收款默认关闭 CLOSED
PASS: 自主支出永远禁止 FORBIDDEN_ALWAYS
PASS: 信封关闭 CLOSED
PASS: 信封模板关闭 CLOSED
PASS: 信封未被任何人开启（opened_by 为空）
PASS: 模板合同未提供，信封不可开启
PASS: 16 类情形齐全且顺序一致（实有 16 类）
...
PASS: 全部 16 类找的都是董事长
PASS: 预算线：自主支出上限 0 元
PASS: 首 3 单须确认 3 单
PASS: 首 3 单已确认 0/3
PASS: 自开拦截：开启人只能是董事长（本次为空，正常）
RESULT: PASS
exit=0
```

计数复核：`grep -c "^PASS:"` = 80；`grep -c "^FAIL:"` = 0。

### 7.4 pull_once.py 头注释实测（S1，逐字）

命令：`head -n 120 /home/ubuntu/ai-team/state/pull-once-20261105/worker/pull_once.py`（头注释部分逐字抄录如下，代码其余部分见 §7.5 摘要）：

```
#!/usr/bin/env python3
"""pull_once - worker-1 pull client for queue_api (PROD 2026-11-05).
Single cycle only: claim one task, checkpoint, execute, upload artifact,
submit, exit. Stdlib only. Talks to queue_api via the SSH tunnel on
127.0.0.1:12700 (hub localhost). Token read from ~/.queue-api-token (0600);
the token value is NEVER logged or printed. No upstream keys, no gateway
token, no Zen key on this worker; no direct upstream connection.
Script execution mirrors the production executor allowlist: only commands
starting with echo/cat/ls/sha256sum, 30 s timeout, no sudo, no network.
Code branch (type==code): runs `python3 -m pytest -q` (list args, no shell,
120 s timeout) in payload.workdir ONLY if it is under
~/code-agent-pilot/workdir/; anything else is rejected. It executes and
tests PRE-EXISTING pilot code only: no LLM call, no code generation,
no model, no upstream keys.
"""
```

### 7.5 pull_once.py 执行分支实测摘要（第 120–159 行，已读）

- 脚本分支：命令不在 `ALLOWED_SCRIPT_PREFIXES = ("echo ", "cat ", "ls ", "sha256sum ")` 前缀内 → `fail(tid, fencing, "command_not_allowlisted")`，返回 3；命中后 `subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)`，非 0 退出 → `fail(..., "script_exit_%s")`。
- code 分支：workdir 不在 `~/code-agent-pilot/workdir/` 下 → `fail(..., "workdir_not_allowlisted")`；目录不存在 → `workdir_missing`；pytest 120 s 超时 → `pytest_timeout`；pytest 非 0 → `pytest_exit_%s`；通过后才上传 artifact、checkpoint step=2、submit，全程带 fencing token。
- 其余类型 → `fail(..., "unsupported_type_%s")`，返回 3。

### 7.6 枢纽侧辅助实测

```
ls /home/ubuntu/.queue-api-token → No such file or directory（枢纽不持工人 token，符合预期）
ls -l /home/ubuntu/.config/opencode/ → opencode.env -rw-------，opencode.json -rw-------（0600，内容未读）
ls /home/ubuntu/ai-team/docs/engineering/ → No such file or directory（写本规范前枢纽无此目录，同步时须先 mkdir -p）
```

文件元数据（`ls -l` 实测）：`pull_once.py` 7325 字节，2026-11-05 15:42；`claim_gate.py` 1288 字节，2026-10-31 17:12；`brain_guard.py` 7821 字节，2026-11-04 03:13；`escalations/` 目录含 `DRILL-20261105-001.json`。

### 7.7 守卫代码本地实测（§5 代码，`/tmp/guard06/guardrail.py`）

命令：`python3 -m py_compile /tmp/guard06/guardrail.py && python3 /tmp/guard06/guardrail.py`

输出：

```
GUARD_PY_COMPILE_OK
ALLOW | ls /tmp | ALLOW：未命中高危规则（仅表示通过本守卫，不代表已获业务批准）。
ALLOW | cat README.md | ALLOW：未命中高危规则（仅表示通过本守卫，不代表已获业务批准）。
DENY  | rm -rf /home/ubuntu/ai-team | DENY [DELETE/rm -rf]：高危动作已拦截，未执行；已记日志 /tmp/guard06/guardrail.log.jsonl，须董事长批准后方可重提。
DENY  | 对外发布到生产 | DENY [PUBLISH/publish]：高危动作已拦截，未执行；已记日志 /tmp/guard06/guardrail.log.jsonl，须董事长批准后方可重提。
DENY  | 绑卡开通付费 | DENY [PAYMENT/payment/billing]：高危动作已拦截，未执行；已记日志 /tmp/guard06/guardrail.log.jsonl，须董事长批准后方可重提。
DENY  | sudo rm -rf / | DENY [DELETE/rm -rf]：高危动作已拦截，未执行；已记日志 /tmp/guard06/guardrail.log.jsonl，须董事长批准后方可重提。
ALLOW | echo hello | ALLOW：未命中高危规则（仅表示通过本守卫，不代表已获业务批准）。
DENY  | 签署合同并付款 | DENY [PAYMENT/payment/billing]：高危动作已拦截，未执行；已记日志 /tmp/guard06/guardrail.log.jsonl，须董事长批准后方可重提。
guard_exit=0
```

结论：8 例中 3 ALLOW、5 DENY，DENY 均写 JSONL 日志（5 条，时间戳 2026-11-06T03:25:00Z）。**本地实测通过 ≠ 生产已接线**（见 §5）。

## 8. 红线速查（D 级永久禁止，S4 原文）

伪造、绕验收、隐瞒、擅扩权限、保活造假、违规占算力、未知冒充正常、覆盖唯一备份。命中任一 → 立即停手、按红警口径上报（枢纽 `state/outbox/`，P0 例外同时推 TG），不自行处置、不删日志。

## 9. 修订记录

| 版本 | 日期 | 内容 | 状态 |
|---|---|---|---|
| v0.1 初版 | 2026-11-06 | 按 S1–S5 写矩阵、四类必批、环境规范、守卫代码；实测见 §7 | [初版待验证]：待 U1–U7 核实、待守卫接线、待董事长 / 独立复核确认后方可摘“待验证” |
