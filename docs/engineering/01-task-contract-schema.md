# 规范 01《任务契约与数据字典规范》

- 状态：**[初版待验证]**（本文已按真源实测写成，但未经复核席独立验收，不得当作已定稿引用）
- 真源（枢纽 `/home/ubuntu/ai-team`，SSH：`ssh arm-agent`）：
  - `scripts/taskcontract_gate.py` —— 8 必填字段门禁（唯一判定实现，stdlib）
  - `state/contract-mandatory-20261105/CONTRACT-TEMPLATE.md` —— 字段口径
  - `state/l3-20261105-contracts/contract-retrywait-rework-20261106.json` —— 真实 code 合同（11 字段）
  - `state/pull-once-20261105/worker/pull_once.py` —— worker-1 执行侧 workdir 白名单
- 实测时间：2026-11-06 11:24:21 CST（03:24:21 UTC），枢纽 `python3 --version` = Python 3.12.3，`pydantic` = 2.14.1
- 实测命令（全部在枢纽上跑，见 §8 原文记录）：`python3 scripts/taskcontract_gate.py --validate <合同>` / `--insert-guard <合同>`

## 1. 强制规则（以 gate 代码为准，文字只作索引）

```text
必填 8 字段（缺一或空值即拒）：task_type, input, output_path, evidence,
                              pass_line, fail_action, verifier, forbidden
  空值定义：None / 空白字符串 / 空 list / 空 dict 均算缺失。
task_type 白名单：llm_doc, script, patrol, timer, code
code 型追加：workdir 非空（顶层 workdir，或 input 为对象时 input.workdir）。
格式错误 JSON：INVALID，exit 1。
多余字段：忽略，不报错。
判定：exit 0 = VALID；exit 1 = INVALID（打印缺失字段）。
insert-guard：同上校验，通过打印 INSERT ALLOWED，否则 INSERT REFUSED；
  REFUSED = 禁止写入 central_tasks，任务留 pending，不可派单。
```

```python
# gate 核心判定（摘自 scripts/taskcontract_gate.py，原样）
REQUIRED_FIELDS = [
    "task_type", "input", "output_path", "evidence",
    "pass_line", "fail_action", "verifier", "forbidden",
]
TASK_TYPE_WHITELIST = {"llm_doc", "script", "patrol", "timer", "code"}

def _is_empty(value):
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False

def _get_workdir(contract):
    workdir = contract.get("workdir")
    if not _is_empty(workdir):
        return workdir
    inp = contract.get("input")
    if isinstance(inp, dict):
        workdir = inp.get("workdir")
        if not _is_empty(workdir):
            return workdir
    return None

def validate(contract):
    """Return a list of error strings; empty list means VALID."""
    errors = []
    if not isinstance(contract, dict):
        return ["contract is not a JSON object"]
    for field in REQUIRED_FIELDS:
        if field not in contract or _is_empty(contract[field]):
            errors.append("missing field: %s" % field)
    task_type = contract.get("task_type")
    if not _is_empty(task_type) and task_type not in TASK_TYPE_WHITELIST:
        errors.append(
            "invalid task_type: %r (allowed: %s)"
            % (task_type, ", ".join(sorted(TASK_TYPE_WHITELIST)))
        )
    if task_type == "code":
        if _is_empty(_get_workdir(contract)):
            errors.append("missing field: workdir (required for code tasks)")
    return errors
```

注意：`scripts/contract_validator.py` 是工具白名单校验器，**不是** 8 字段合同校验器，不得互相替代（gate 文件头原话）。

字段口径（`CONTRACT-TEMPLATE.md` 原文要点）：

```text
input：输入来源路径/任务号/上一任务 id，禁止口头输入
output_path：产物写到哪里（相对路径，禁止只写“已完成”）
  —— 注：模板写“相对路径”，但真实 code 合同用的是枢纽绝对路径
     /home/ubuntu/ai-team/scripts/retrywait-scan-20261105.py，且 gate VALID；
     以 gate 代码为准：gate 不校验相对/绝对，只判非空。
evidence：可机器检查的证据，至少 2 项：
  artifact_sha（64 hex）+ artifact_bytes（>= min_bytes）/ tests（命令 + 通过数/总数）
  / counts（条数/id 唯一/行数等脚本判定数，禁止人工数）
pass_line：通过线，如 tests 全绿、条数=30、bytes>=min_bytes、sha 与产物一致
fail_action：不过则 RETRY_WAIT/QUARANTINED/重派，不记 DONE、不人工改 DONE
verifier：判定方 = c2-verifier（确定性）+ 独立复核席（不同供应商模型，仅 code/决策类），禁止自评自转正
forbidden：禁止项（对外发布/花钱/删数据/不可逆/贴密钥原文，命中即拒）
verifier 只按 contract 判 DONE，不认工人自述；自述 DONE 无证据 = 假完成。
```

## 2. 标准派单 JSON 示例

### 2.1 code 型——真实合同原样（已过 gate，VALID）

文件：`state/l3-20261105-contracts/contract-retrywait-rework-20261106.json`，11 个字段。
`task_id`、`lane`、`workdir` 为 8 必填之外的附加字段，gate 忽略。

```json
{
  "task_id": "retrywait-fallback-rework-20261106",
  "lane": "infra",
  "task_type": "code",
  "workdir": "~/code-agent-pilot/workdir/",
  "output_path": "/home/ubuntu/ai-team/scripts/retrywait-scan-20261105.py",
  "input": "返工：原任务 retrywait-fallback-impl-20261105（4390786b-4646-4732-8a2c-dd66bef99171）为假 DONE——交付物只是 156 字节 pytest 通过输出（sha 7ff3a3d9…），扫描器文件根本不存在，已记入 reject-ledger。按枢纽 state/expert-brief-decisions-20261105/RETRY-WAIT-FALLBACK.md 重新实现总调度定时扫描器 scripts/retrywait-scan-20261105.py：扫描 central_tasks 中 RETRY_WAIT 行，超等待阈值、无活跃 claimer 时重派（RETRY_NOW / FALLBACK / BLOCKED 上报大管家 / TERMINAL_FAILED 归档）；Watchdog 检测无人认领/租约过期/卡死；覆盖 timer/patrol，不只文档任务。另做 claimer PAUSED 演练：人为制造 claimer PAUSED 场景，任务最终被重认领或明确进 BLOCKED，留演练日志。认领前置检查：先查本机有无执行本任务所需配置（可读枢纽 state、可改 hub scripts、可调模型）；无则一次性报缺哪项，不要认领后空转。注意：枢纽 executor 的 code 型任务只做 defer，实际执行由 worker-1 经 queue_api/pull_once 拉活；worker-1 pull_once 目前只有 pytest 路径、没有代码生成路径（生成代理接口未建），若本机不能改 hub 代码，一次性报缺项并留 READY，绝不提交 pytest 通过输出冒充交付物。定时器接线（每分钟扫描）须另行定案，本任务不擅自建生产 cron，只交付脚本 + --dry-run + 演练日志。",
  "evidence": "扫描器文件存在且 py_compile 通过，附 sha256；--dry-run 输出含当前 RETRY_WAIT 清单（逐项列出，与库内实时状态一致）；claimer PAUSED 演练日志：任务最终被重认领或明确进 BLOCKED；无 pytest-only 伪造产物。",
  "pass_line": "扫描器文件存在且 py_compile 通过；演练日志证明 PAUSED claimer 场景下任务不无限停 RETRY_WAIT；证据均为实读文件内容，不只 bytes。",
  "fail_action": "任一证据缺失则 RETRY_WAIT，不报 DONE；实现不了则一次性报缺项并留 READY；绝不提交 pytest 通过输出冒充交付。",
  "verifier": "c2-verifier 内容核验（实读扫描器文件与演练日志，抽查 3 处以上，不只看 sha/bytes）+ 本席独立验收后再报董事长。注意：现行 c2-verifier 只验 sha/bytes，不验内容，内容核验须由验收方实读完成。",
  "forbidden": ["不伪造 DONE", "不提交 pytest 通过输出冒充交付物", "不绕过合同门禁", "不改生产 schema（central_tasks_transition_guard 不动）", "不绑死单人认领", "不擅自建生产 cron", "不花钱、不对外发布"]
}
```

### 2.2 standard 型（timer）——构造示例，已在枢纽过 gate，VALID

文件：枢纽 `/tmp/contract-standard-example-20261105.json`（临时示例，非生产合同）。
timer 无 workdir 要求，8 字段齐全即 VALID。

```json
{
  "task_id": "spec01-standard-example-timer",
  "task_type": "timer",
  "input": "state/pull-once-20261105/EVIDENCE-20261105-timer.md",
  "output_path": "/home/ubuntu/ai-team/state/pull-once-20261105/EVIDENCE-20261105-timer.md",
  "evidence": "crontab -l 输出含 */2 wake_scheduler --tick 行；wake-log.jsonl 有 tick 记录",
  "pass_line": "tick 唤醒 worker-1 且 pull 周期完成；仅 worker-1 被唤醒",
  "fail_action": "失败进 RETRY_WAIT（最多 3 次），不记 DONE",
  "verifier": "c2-verifier（确定性）",
  "forbidden": ["不唤醒 worker-1 之外的工人", "不花钱", "不对外发布"]
}
```

## 3. workdir 绝对路径映射表

| 合同写法 | 在哪解析 | 解析后绝对路径 | 依据与状态 |
|---|---|---|---|
| `~/code-agent-pilot/workdir/` | 枢纽 gate | **不展开**，只判非空 | gate 代码：`_get_workdir` 只取字符串，不调 `expanduser`。实测 VALID |
| `~/code-agent-pilot/workdir/` | worker-1 pull_once | `/data/home/code-agent-pilot/workdir/` | pull_once 用 `os.path.expanduser` + `realpath`；worker-1 家目录实测为 `/data/home` |
| `/data/home/code-agent-pilot/workdir/` | worker-1 本机 | 同左（已是绝对路径） | 拒单台账实测：`workdir=/data/home/code-agent-pilot/workdir`；`EVIDENCE-20261105-timer.md` 实测：`workdir=/data/home/code-agent-pilot/workdir/pilot-20261105-code-1` |
| `/home/ubuntu/ai-team/...` | 枢纽侧（output_path、state、scripts） | 枢纽绝对路径 | 枢纽家目录 `/home/ubuntu`；retrywait 合同 `output_path=/home/ubuntu/ai-team/scripts/retrywait-scan-20261105.py` |

关键坑（实测确认）：

```text
1. 同一字符串 "~/code-agent-pilot/workdir/" 在两地含义不同：
   枢纽 gate 只判非空、不展开；worker-1 pull_once 才展开成 /data/home/...。
   禁止把枢纽路径当 worker 路径，也禁止把 worker 路径写进枢纽侧 output_path。
2. worker-1 家目录是 /data/home，不是 /home/<user>，不是 /tmp。
   证据：枢纽 state/window-b-20261104/REPORT-20261104.md 称“工人 home 实测为
   /data/home（持久盘），非 /tmp”；reject-ledger 与 EVIDENCE-20261105-timer.md
   的 workdir 实测值均为 /data/home/code-agent-pilot/workdir/...。
3. output_path 与 workdir 分属两台机器：
   output_path = 枢纽侧交付物位置（/home/ubuntu/ai-team/...）；
   workdir = worker-1 本机执行目录（/data/home/code-agent-pilot/workdir/...）。
   code 型合同必须同时写对两边，缺一边即不合格。
```

pull_once workdir 白名单（执行侧硬规则，`state/pull-once-20261105/worker/pull_once.py`）：

```python
# Code branch: execute + test PRE-EXISTING pilot code only.
# No LLM call, no code generation, no model, no upstream keys.
# Workdir allowlist: only under ~/code-agent-pilot/workdir/.
wd_raw = (payload.get("workdir") or "")
wd = os.path.expanduser(wd_raw)
base = os.path.realpath(os.path.expanduser("~/code-agent-pilot/workdir"))
wd_real = os.path.realpath(wd)
if not wd_raw or (wd_real != base and not wd_real.startswith(base + os.sep)):
    fail(tid, fencing, "workdir_not_allowlisted")
    return 3
if not os.path.isdir(wd_real):
    fail(tid, fencing, "workdir_missing")
    return 3
out = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                     cwd=wd_real, capture_output=True, text=True, timeout=120)
```

## 4. code_gen 与 standard 字段区别表

“code_gen”指 code 型任务的生成/执行路径，“standard”指其余 4 类（llm_doc / script / patrol / timer）的 8 字段合同路径。

| 维度 | code 型（code_gen 路径） | standard 型（llm_doc / script / patrol / timer） |
|---|---|---|
| task_type | `code` | `llm_doc`、`script`、`patrol`、`timer` |
| 必填字段 | 8 字段 + `workdir`（顶层或 `input.workdir`） | 仅 8 字段 |
| workdir | 必填；枢纽 gate 判非空；worker-1 只认 `~/code-agent-pilot/workdir/` 之下，否则 `workdir_not_allowlisted` 拒单 | 无此字段；写了也被 gate 忽略 |
| output_path | 枢纽侧代码文件绝对路径，如 `/home/ubuntu/ai-team/scripts/*.py` | 枢纽侧证据/文档路径，如 `state/.../EVIDENCE-*.md` |
| input | 任务说明 + 返工/前置依据，可长文本（见 §2.1） | 输入来源路径/任务号/上一任务 id，禁止口头输入 |
| 执行方式（实测） | pull_once code 分支：只对**已存在**的 pilot 代码跑 `pytest -q`（120s 超时），**不调 LLM、不生成代码** | script 分支：只允许 `echo / cat / ls / sha256sum` 开头命令（30s 超时）；其余类型由认领方按合同产出文档/巡检结果 |
| 代码生成 | pull_once 现无生成路径（docstring 原话：no code generation；1b 合同 pass_line 原话：code generation via opencode/gateway is a later step）。生成代理接口未建是已知缺口，见 retrywait 合同 input 原文 | 不涉及 |
| evidence 侧重 | 文件存在 + `py_compile` 通过 + sha256 + 演练日志；禁止 pytest-only 输出冒充交付物 | sha/bytes、tests 通过数、counts 脚本判定数、crontab / 日志原文 |
| verifier | c2-verifier + 独立复核席（不同供应商模型，仅 code/决策类） | c2-verifier（确定性）；独立复核席仅 code/决策类 |
| 典型失败 | 缺 workdir（gate INVALID）；workdir 不在白名单（`workdir_not_allowlisted`）；目录不存在（`workdir_missing`）；pytest 非 0 | 缺 8 字段任一（gate INVALID）；证据只有自述/截图（verifier 拒） |

## 5. 完整 JSON Schema（与 gate 代码同义）

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://ai-team.local/schemas/task-contract/v1",
  "title": "TaskContract",
  "type": "object",
  "required": [
    "task_type", "input", "output_path", "evidence",
    "pass_line", "fail_action", "verifier", "forbidden"
  ],
  "properties": {
    "task_type": {
      "type": "string",
      "enum": ["llm_doc", "script", "patrol", "timer", "code"]
    },
    "input": {
      "description": "字符串或对象；对象时可含 workdir（code 型可用 input.workdir 顶替顶层 workdir）",
      "anyOf": [
        { "type": "string", "minLength": 1 },
        {
          "type": "object",
          "properties": { "workdir": { "type": "string", "minLength": 1 } }
        }
      ]
    },
    "output_path": { "type": "string", "minLength": 1 },
    "evidence": {
      "description": "可机器检查的证据描述；字符串或非空数组均可，gate 只判非空",
      "anyOf": [
        { "type": "string", "minLength": 1 },
        { "type": "array", "minItems": 1 }
      ]
    },
    "pass_line": { "type": "string", "minLength": 1 },
    "fail_action": { "type": "string", "minLength": 1 },
    "verifier": { "type": "string", "minLength": 1 },
    "forbidden": {
      "type": "array",
      "minItems": 1,
      "items": { "type": "string", "minLength": 1 }
    },
    "workdir": { "type": "string", "minLength": 1 },
    "task_id": { "type": "string", "minLength": 1 },
    "lane": { "type": "string", "minLength": 1 }
  },
  "allOf": [
    {
      "if": { "properties": { "task_type": { "const": "code" } } },
      "then": {
        "anyOf": [
          { "required": ["workdir"] },
          { "properties": { "input": { "required": ["workdir"] } }, "required": ["input"] }
        ]
      }
    }
  ]
}
```

与 gate 代码的差异说明（以 gate 为准，Schema 只是同一规则的另一种写法）：

```text
1. gate 的“非空”含空白字符串（strip 后为空算缺失）；本 Schema 用 minLength: 1，
   不剔除纯空白字符串。严格校验请用 §6 Pydantic 代码（strip 校验）或直接跑 gate。
2. gate 忽略多余字段；本 Schema 未设 additionalProperties: false，与 gate 一致。
3. input 为对象时 gate 只要求对象非空，不校验其内部字段；Schema 的 input 对象分支
   仅为 workdir 顶替情形建模，不作额外强制。
```

## 6. Python 校验代码

枢纽实测：`pydantic 2.14.1` 可用。首选 Pydantic v2；无 pydantic 时用 stdlib fallback（与 gate 同义）。

```python
# task_contract_model.py — TaskContract 校验（Pydantic v2）
# 用法：python3 task_contract_model.py <contract.json>  ；exit 0 = VALID，exit 1 = INVALID
from __future__ import annotations

import json
import sys

from pydantic import BaseModel, field_validator, model_validator

TASK_TYPE_WHITELIST = {"llm_doc", "script", "patrol", "timer", "code"}
REQUIRED_FIELDS = [
    "task_type", "input", "output_path", "evidence",
    "pass_line", "fail_action", "verifier", "forbidden",
]


def _non_empty_str(v, field: str) -> str:
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"missing field: {field} (empty string)")
    return v


class TaskContract(BaseModel):
    task_type: str
    input: str | dict
    output_path: str
    evidence: str | list
    pass_line: str
    fail_action: str
    verifier: str
    forbidden: list[str]
    # 附加字段：gate 忽略，本模型显式收下，避免误判
    workdir: str | None = None
    task_id: str | None = None
    lane: str | None = None

    model_config = {"extra": "ignore"}  # 多余字段忽略，与 gate 一致

    @field_validator("task_type")
    @classmethod
    def _check_task_type(cls, v: str) -> str:
        _non_empty_str(v, "task_type")
        if v not in TASK_TYPE_WHITELIST:
            raise ValueError(
                f"invalid task_type: {v!r} (allowed: {', '.join(sorted(TASK_TYPE_WHITELIST))})"
            )
        return v

    @field_validator("output_path", "pass_line", "fail_action", "verifier")
    @classmethod
    def _check_str(cls, v: str, info) -> str:
        return _non_empty_str(v, info.field_name)

    @field_validator("input")
    @classmethod
    def _check_input(cls, v):
        if isinstance(v, str):
            return _non_empty_str(v, "input")
        if isinstance(v, dict):
            if not v:
                raise ValueError("missing field: input (empty object)")
            return v
        raise ValueError("missing field: input (must be string or object)")

    @field_validator("evidence")
    @classmethod
    def _check_evidence(cls, v):
        if isinstance(v, str):
            return _non_empty_str(v, "evidence")
        if isinstance(v, list):
            if not v:
                raise ValueError("missing field: evidence (empty list)")
            return v
        raise ValueError("missing field: evidence (must be string or list)")

    @field_validator("forbidden")
    @classmethod
    def _check_forbidden(cls, v: list) -> list:
        if not v:
            raise ValueError("missing field: forbidden (empty list)")
        return v

    @field_validator("workdir")
    @classmethod
    def _check_workdir(cls, v):
        if v is not None and (not isinstance(v, str) or not v.strip()):
            raise ValueError("missing field: workdir (empty string)")
        return v

    @model_validator(mode="before")
    @classmethod
    def _check_code_workdir(cls, data):
        # mode="before"：字段校验失败时 after 校验不会跑，workdir 错误会被吞掉，
        # 故在原始 dict 上先判。代价：before 抛错会截断后续字段校验，code 型
        # 缺 workdir 时只报 workdir 一项；要完整缺失清单用 stdlib fallback
        #（直接复用 gate validate()）。VALID/INVALID 与 exit 码与 gate 一致。
        if isinstance(data, dict) and data.get("task_type") == "code":
            wd = data.get("workdir")
            if (wd is None or (isinstance(wd, str) and not wd.strip())) and isinstance(
                data.get("input"), dict
            ):
                wd = data["input"].get("workdir")
            if wd is None or (isinstance(wd, str) and not wd.strip()):
                raise ValueError("missing field: workdir (required for code tasks)")
        return data


def validate_file(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"INVALID: cannot read/parse {path}: {exc}"]
    try:
        TaskContract.model_validate(raw)
    except Exception as exc:  # ValidationError
        return [
            "INVALID: %s: %s"
            % (".".join(str(x) for x in e["loc"]) or "__root__", e["msg"])
            for e in exc.errors()  # type: ignore[union-attr]
        ]
    return []


if __name__ == "__main__":
    errs = validate_file(sys.argv[1])
    if errs:
        print("; ".join(errs))
        sys.exit(1)
    print("VALID")
    sys.exit(0)
```

```python
# task_contract_stdlib.py — 无 pydantic 时的 stdlib fallback（与 gate 同义）
# 最稳妥做法：直接复用枢纽 gate 的 validate()，不另写一份判定逻辑。
# 用法：python3 task_contract_stdlib.py <contract.json>
import json
import sys

sys.path.insert(0, "/home/ubuntu/ai-team/scripts")  # 枢纽路径；在别处跑时改成本地 gate 路径
from taskcontract_gate import validate  # noqa: E402  (stdlib only 的 gate 模块)

with open(sys.argv[1], encoding="utf-8") as f:
    try:
        contract = json.load(f)
    except json.JSONDecodeError as exc:
        print(f"INVALID: malformed JSON: {exc}")
        sys.exit(1)

errors = validate(contract)
if errors:
    print("INVALID: " + "; ".join(errors))
    sys.exit(1)
print("VALID")
```

## 7. 派单流程（代码即流程）

```bash
# 派单方（总调度 OpenClaw）写入 central_tasks 之前必须先过 gate：
cd /home/ubuntu/ai-team
python3 scripts/taskcontract_gate.py --insert-guard <contract.json>
# INSERT ALLOWED  -> 允许写入 central_tasks
# INSERT REFUSED  -> 禁止写入，任务留 pending，补字段后重验
# 校验只是门禁，不代替 verifier 判定 DONE；verifier 只按 contract 判 DONE。
```

## 8. 实测记录（原文粘贴，未改写）

实测环境：`ssh arm-agent`，`cd /home/ubuntu/ai-team`，2026-11-06 11:24:21 CST，Python 3.12.3。

```text
$ date -u "+%Y-%m-%dT%H:%M:%SZ"; date "+%Y-%m-%d %H:%M:%S %Z"; python3 --version
2026-11-06T03:24:21Z
2026-11-06 11:24:21 CST
Python 3.12.3

$ python3 scripts/taskcontract_gate.py --validate state/l3-20261105-contracts/contract-retrywait-rework-20261106.json
VALID
exit=0

$ python3 scripts/taskcontract_gate.py --validate /tmp/contract-missing-field-20261105.json
INVALID: missing field: verifier; missing field: forbidden; missing field: workdir (required for code tasks)
exit=1
# 缺字段合同内容（本次实测临时造，/tmp/contract-missing-field-20261105.json）：
# {"task_id":"spec01-missing-field-demo","task_type":"code","input":"缺字段演示",
#  "output_path":"/tmp/x.py","evidence":"无","pass_line":"无","fail_action":"RETRY_WAIT"}
# 故意缺 verifier、forbidden；code 型又缺 workdir，故三条错误并列。

$ python3 scripts/taskcontract_gate.py --validate /tmp/contract-standard-example-20261105.json
VALID
exit=0

$ python3 scripts/taskcontract_gate.py --insert-guard state/l3-20261105-contracts/contract-retrywait-rework-20261106.json
INSERT ALLOWED: contract valid
exit=0

$ python3 -c "import pydantic; print('pydantic', pydantic.VERSION)"
pydantic 2.14.1
```

已发现的不一致（实测，非推断）：

```text
$ python3 scripts/taskcontract_gate.py --validate state/l3-20261105-contracts/1b-pullonce-timer.json
INVALID: missing field: workdir (required for code tasks)
exit=1
# 该合同 task_type=code，但无 workdir 字段（11-05 当时 gate 的 workdir 规则
# 尚未成为强制项或该合同未同步）。现行 gate 下它不能入队；
# 处置：补 workdir 后重验，或改 task_type，二选一，不得直接放行。
```

```text
$ python3 /tmp/task_contract_model.py <合同> / python3 /tmp/task_contract_stdlib.py <合同>
# §6 两份代码均已在枢纽实测，与 gate 判定一致（VALID/INVALID 与 exit 码全同）：
#   retrywait 真实合同       -> VALID，exit 0（两份代码一致）
#   standard timer 示例      -> VALID，exit 0（两份代码一致）
#   缺字段合同               -> INVALID，exit 1
#     stdlib（= gate 原文）：missing field: verifier; missing field: forbidden;
#             missing field: workdir (required for code tasks)
#     pydantic：只报一项 —— INVALID: __root__: Value error, missing field:
#             workdir (required for code tasks)。before 校验先抛错，截断了
#             verifier/forbidden 的字段校验；exit 码与 gate 一致，完整缺失
#             清单以 gate/stdlib 输出为准。
#   1b-pullonce-timer.json  -> INVALID，exit 1（两份代码一致，缺 workdir；
#             pydantic 同样只报 workdir 一项）
```

## 9. 未实测

```text
- §5 JSON Schema 未用 jsonschema 库逐项验证（枢纽是否装 jsonschema 未查）。
- code_gen 生成路径（opencode/gateway 生成代码）不存在于 pull_once 是实测结论；
  但生成路径的完整设计（code_spec -> code_gen -> code_test -> code_review ->
  verifier）只见于既往记录，本次未在枢纽实测复核，不得当作已实现引用。
- 本规范本身状态仍为 [初版待验证]，待复核席独立验收后改状态。
```
