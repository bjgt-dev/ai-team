# Eval OS 第二阶段接线 + 三考试派发实施日志（g5-eval-wire-log-20261111）

> 任务 slug：`eval-os-mvp-phase2`；二阶段批准 plan_id：`23085d4b-d440-4b07-b68d-1a6656dfa9a7`，2026-11-12 06:48 CST 过期。
> 门禁回执（2026-11-11 14:13 CST 新取）：memory `7ba073c1-1323-4f74-87f1-5c31f718925f`、kb `dcdfd151-a713-4579-8bd2-f2b24a8afc7a`；本地 implementation-gate PASS（exit 0）。
> 本日志只追加。本地：`~/workspace/eval-os-phase2/g5-eval-wire-log-20261111.md`；枢纽同步：`~/ai-team/state/eval-os-phase2/g5-eval-wire-log-20261111.md`。

## 0. 已定事项（实查结论）

- **VERDICT_ISSUED 的 trace_id 口径（定死）**：`verdict:<eval_run_id>:<eval_id>`（签发前占位值）。
  理由：trace-contract 稿定 queue_api 在 TASK_CREATED 时生成真实 trace_id（M3 未接线，签发时真实 trace_id 不存在）；
  占位值明示来源，不虚构 queue_api 风格 UUID。运行器归集时以 TASK_CREATED 的真实 trace_id 为准，
  按（eval_run_id，eval_id，task_slug）关联。VERDICT_ISSUED 行的 `task_id` 写 NULL（eval_traces.task_id 为 UUID 型，
  任务行尚未入库）；`status` 写 `READY`（该 verdict 授权的派发将建 READY 任务）；`actor=eval-gate`。
  **更正（2026-11-11 Case A 首签实测）**：`event_id` 是 GENERATED ALWAYS 标识列，INSERT 不得显式写值
  （显式 nextval 即报 `cannot insert a non-DEFAULT value into column "event_id"`）；签发代码已改成不列 event_id，
  由库自动生成。首签的 VERDICT_ISSUED 行已按签发日志行手工补写（event_id=1，ts=签发时刻 2026-11-11T06:29:41Z）。
  若 trace 写库失败：verdict 文件已落盘有效，签发代码大声记日志（issue log 行记 `trace_ok=false`），由运行器按日志行补写事件。
- **contract_hash 口径**：canonical hash = `sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False))`，
  与 dispatch_task.py `_canonical_hash`（L159–162）一字一致；收据与触发器都认这个，不是文件字节 sha。
- **Case B 真实拦截点（实查，与运行计划预测不同）**：queue_api 的 QUEUE_API_REVIEW_ENFORCE=1（进程环境实查）。
  篡改 bundle 在 /v1/submit 时由 `_ensure_review`（规范解析器 `load_artifact`）以
  `artifact-extract-fail:bundle: sha mismatch for <文件>` 拒收 → review gate REVIEW-BLOCKED → submit 409 review_blocked；
  verifier 的 gates234 不会见到篡改包（review 门先拦）。worker 在 submit 409 后不调 /v1/fail、直接退出（return 3），
  任务留 CLAIMED，租约过期后重提合法包（运行计划 §2.4 允许的“修正重提后 DONE”终态）。
  干跑须实证此链；Case B 通过标准仍为“篡改包永不 DONE”（最终 artifact_sha ≠ 篡改包 sha）。
- **Case C F1 盖戳**：/v1/fail 处理器写 RETRY_WAIT/QUARANTINED 后调 `_stamp_event`（F1，failure_id 12），
  `pytest_exit_5` → `_FAIL_CLASS_EXACT` → `F-EMPTY`；central_events payload 将含 failure_class。干跑/实跑时核验。
- **blocked-signatures**：`eval` 在 blocked-signatures.json 中 0 命中，三 slug 推导签名 `eval-EVAL` 未被封，可派发。
- **G3/G4 核验结论（代码已读，无改动）**：taskcontract_gate.py 白名单含 eval；gate-svc 第 2 门对 eval 类型认 eval_id + eval_run_id。
  三 Case 本身是 code 任务，走正常 code 路 + eval verdict，不受 G3/G4 影响。只登记，不另改代码。
- **llm_doc 两缺陷**（Plan A 最终回报 §⑥）：只登记，不在本轮扩范围修。

## 1. G1 签发代码（eval-gate.py 新增 issue-verdict 子命令）

- 备份：枢纽 `scripts/eval-gate.py` 改前备份（核 sha `283671c7…`），改后 py_compile。
- P1：模板文件在 `<base>/ceo-seat/state/butler-verdicts/eval-verdict-template-20261111.md` 且含 `状态：已生效`，日期 ≤2026-12-11。
- P2：plan_approvals 有 `task_slug='eval-os-mvp-phase2'` 且 plan_id 一致、ts 在 24h 内（管理通道 docker exec）。
- P3：四张 eval 表存在（`to_regclass` 计 4）。
- P4：合同过 `taskcontract_gate.py --insert-guard` + 算 canonical contract_hash；合同顶层 eval_id/eval_run_id 须与签发参数一致（否则 G2 门禁必 403，verdict 会被浪费）。
- P5：eval_run_id 格式 `eval-run-<YYYYMMDD>-<序号>`。
- P6：目标文件不存在 + 全目录扫描无同（eval_run_id，eval_id）已签发文件；同名即拒绝，永不覆盖。
- 写盘：tmp + os.replace，只写 ceo-seat 目录；字段按模板 §3.1 定死（verdict=`eval-通过`、eval=true、conditions=[]、
  issued_by=`eval-gate advance`、valid_until=issued_at+24h、template_version=1、scope 固定文案）。
- 签发后两条记录：① 日志行进 `ceo-seat/state/butler-verdicts/eval-verdict-issue-log.jsonl`；
  ② VERDICT_ISSUED 事件进 eval_traces（见 §0 口径）。
- 单测：只在 tmp base 下跑（合成合同 + 假模板 + monkeypatch db_query），含负向用例（P6 重签拒、P5 格式拒、P4 合同不过拒）；
  绝不往生产 verdicts 目录写测试文件。

## 2. G2 门禁接线（dispatch-gate-hub.py _verdict_ok + main 合同加载）

- 备份：枢纽 `scripts/dispatch-gate-hub.py` 改前备份（核 sha `dc5f30f4…`），改后 py_compile。
- eval 分支插在大管家分支之前，大管家分支一字不动；严格按模板 §6.2 伪代码；
  合同缺 eval 三项时 fail-closed；语义冒充（eval=true + 通过/有条件通过）记冒充日志进 `state/dispatch-gate-ledger.jsonl`
  （该 ledger 已确认存在，15566 字节，2026-11-11 13:51），事件名 `eval-verdict-impersonation`。
- 金丝雀 10 用例（模板 §6.3）：1–2 回归放行、3 合规 eval 放行、4 冒充拒收+记日志、5 EVAL_PASS 拒收、6 EVAL-006 拒收、
  7 run 不符拒收、8 contract_hash 不符拒收、9 conditions 非空拒收、10 缺 verdict 文件拒收。
- 审计须仍显示 dispatch-gate-hub WIRED。

## 3. eval_cases 三行插入（EVAL-001/003/005，code 类型）

- 待写 SQL 文件，先审后执行（管理通道 docker exec -u postgres）。eval_traces.eval_id 外键指向 eval_cases，须在签发前插入。
- 内容以 `eval-os-first-cases-20261110.md` 为准（input/success_criteria/constraints/verify_method）。

## 4. 派发顺序（一次一个，过 Gate 检查再派下一个）

- Case A（EVAL-001）：test_kids5_1.py（12 边界测试）→ hub 预跑 47 passed → PLAN/合同/预检 → 签发 → pipeline-dispatch →
  轮询至 DONE → 归集 trace → Baseline 快照（version=1）→ Gate 检查。
- Case B（EVAL-005）：先干跑篡改替换流程（合成任务 + 合成 verdict，验证 review gate 拦截）→ 再签发真实 verdict、派发、替换、验证拒收 → 写 eval_failures → Gate 检查。
- Case C（EVAL-003）：空 workdir 派发 → 验证 exit 5 + F-EMPTY + 业务失败账本行 → 等重试走完进 QUARANTINED → Gate 检查。
- run：`eval-run-20261111-01`；slugs：`eval-EVAL-001-20261111-01` / `eval-EVAL-005-20261111-01` / `eval-EVAL-003-20261111-01`。

## 5. 实施结果（只追加）

### 5.1 G1（2026-11-11 ~15:0x CST）
- 备份：`scripts/eval-gate.py.bak-20261111-g1`（改前 sha `283671c7…` 已核）。
- 改动：新增 `issue-verdict` 子命令（P1–P6 + 原子写盘 + 日志 + VERDICT_ISSUED 事件），约 176 行；原有阶段状态机一字未动。
- 单测：`/tmp/evalgate-test-issue-verdict.py`，29/29 通过（含 P6 重签拒、P5 格式拒、P4 合同不过拒、P2 无有效行拒、原有 init/status 回归）。
- 部署：hub sha `5e7b9ec9d03fa4bb429ded5c01a9498d5cc39fb6b8de17f588d0d57d935386ee`，py_compile OK；
  生产冒烟（非法 run 格式）拒绝 exit 1，未写任何生产文件；当时 verdicts 目录无 eval verdict（预期）。
- VERDICT_ISSUED trace 口径按 §0（占位 `verdict:<run>:<eval>`，task_id NULL，status READY，event_id 显式 nextval）。

### 5.2 G2（2026-11-11 ~15:1x CST）
- 备份：`scripts/dispatch-gate-hub.py.bak-20261111-g2`（改前 sha `dc5f30f4…` 已核）。
- 改动：`_verdict_ok` 加 eval 分支（大管家分支一字不动）+ main() 加载合同取 eval 三项 + canonical hash；
  冒充日志写 `state/dispatch-gate-ledger.jsonl`（已确认存在）；合同缺三项时 fail-closed。
- 金丝雀：`/tmp/evalgate-test-g2-canary.py`，13/13 通过（模板 §6.3 十用例 + 合同缺三项 fail-closed 附加项 + 冒充日志断言）。
- 部署：hub sha `2e600ef57a3d20d94dd649144db88c51b11c955fb503e6669278aac07cd28ba3`，py_compile OK。
- 审计：`gate-wiring-audit-db.py` 19/19 WIRED，dispatch-gate-hub 在列（last_block 2026-11-10T14:15:00Z）。

### 5.3 G3/G4 核验（无改动）
- 见 §0：taskcontract_gate.py 白名单含 eval；gate-svc 第 2 门对 eval 类型认 eval_id + eval_run_id；三 Case 走 code 路 + eval verdict。

### 5.4 G1 修复：event_id 标识列（2026-11-11 ~15:3x CST）
- 首签实测：VERDICT_ISSUED 写库失败（event_id 为 GENERATED ALWAYS，显式 nextval 被拒）；verdict 文件本身有效，
  签发日志行记 trace_ok=false（设计内的 fail-open 路径已触发一次）。
- 修复：eval-gate.py 的 INSERT 不再列 event_id；单测断言同步改；单测重跑 29/29；重新部署 hub sha `2d7ac93e…`，py_compile OK。
- 补写：Case A 的 VERDICT_ISSUED 行已按签发日志行补写（event_id=1，trace_id=`verdict:eval-run-20261111-01:EVAL-001`，
  ts=2026-11-11T06:29:41Z，与签发时刻一致）。

### 5.5 Case A 派发（2026-11-11 14:29 CST 起）
- 合同：`state/pipeline-formal-20261106/eval-EVAL-001-20261111-01-contract.json`，insert-guard 通过；
  contract_hash=`561c621657f431d97139f35a7bbb401dc2dae38436ac01b7a17709f7e8660d7d`（签发侧与本地重算一致）。
- verdict：`ceo-seat/state/butler-verdicts/eval-EVAL-001-20261111-01.json`，issued_at 2026-11-11T06:29:41Z，
  valid_until 2026-11-12T06:29:41Z；签发日志行 1 条（trace_ok=false，见 5.4，已补写）。
- 门禁：pipeline-dispatch GATE_PASS（PLAN/CONTRACT/VERDICT eval-verdict=eval-通过/PREFLIGHT 四项全过）；
  gate-svc 收据 7 门全列；任务行 UUID `534b68d3-2a61-428c-8f12-bc77db99807d`，READY 入库。
- provision：5 文件下发 worker workdir `~/code-agent-pilot/workdir/eval-EVAL-001-20261111-01`，
  sha 与 hub 预跑文件一致（d107ded6…/6f5e9c19…/5077f58f…/75db3836…/009c7eb8…）。
- hub 预跑：pytest 47 passed（0.04s），日志 `state/eval-os-phase2/case-a/pytest-47passed-20261111.log`。
- 轮询：READY → CLAIMED（worker-1 已认领），待 DONE。

### 5.6 Case A 首轮失败：verifier 内联 gate 4 拒收（2026-11-11 14:32 CST）
- 现象：VERIFYING → RETRY_WAIT（retry=1/3），artifact_sha=`80ca0c1e3142…`；verifier 五门（gates234）全过，
  但内联五门 gate 4 拒收：`gate4: red-green: tests PASS on stub implementation (tests are vacuous)`。
- 根因（已复现确认，非推断）：合同的 `output_path` 写了测试文件（test_kids5_1.py）且 `outputs` 含 5 个文件（含 2 个测试文件）。
  内联 `_py_source_candidates` 取 output_path + outputs[] 全部 .py 作源码候选（不排除测试文件），
  `_gate4_redgreen` 把候选全部打桩（含测试文件自身——打桩后测试函数体变 `pass`，47 个测试空过，pytest exit 0），
  故判“tests are vacuous”。复现：原合同 `_gate4_redgreen` = ('reject', 同文)；修正形状 = ('pass', 'tests fail on stub, pass on real source')。
  责任：协调员合同写错（沿用了上一轮 §2.6“output_path = 测试文件”的结论，未核对内联 gate 4 的打桩范围）；
  系统行为正确（verifier 按既定规则拦截，事件链完整）。
- 修正形状（已验证）：`output_path` = case-a/immersion_engine.py（源码文件，同目录，reference_dir 不变）；
  `outputs` = 3 个源码文件；`test_files` 不变（2 个测试文件）；`code_files` 不变（5 个，decision-code-gate 只查存在）。
- 任务终态：该任务行（`534b68d3…`）合同已锁进 payload，重试必同错，3 次后进 QUARANTINED（确定性失败，非偶发）。
  central_events 链完整：CREATED→READY→DISPATCHING→CLAIMED→RUNNING→VERIFYING→RETRY_WAIT，
  末事件 payload 已盖戳 `{"reason": "c2-verifier: gate4: ...", "failure_class": "F-VERIFY"}`。
- 影响与待定夺：首轮 verdict 已用掉（一次性，绑定旧 contract_hash `561c6216…`）；P6 禁止同一（run，eval）重签；
  模板 §1 规定后续批次须另行确认。Case A 重试须开新 run（`eval-run-20261111-02`）+ 新 task_slug + 新合同（修正形状）+ 新 verdict，
  **须父席定夺，不得擅自开新 run**。B/C 是否随 A 移到新 run，也待父席定。

### 5.7 回报时点状态（2026-11-11 14:34 CST）
- Case A 任务行 `534b68d3…`：RETRY_WAIT，retry_count=1/3，重试进行中；按既定机制 3 次同错后进 QUARANTINED。
  轮询驱动仍在跑（30 分钟超时，日志 `state/eval-os-phase2/case-a/dispatch-20261111.log`），其终态不改变已定结论，
  故先行回报、不等终态。终态以该日志 + central_tasks 为准。
- **终态确认（2026-11-11 14:4x CST，轮询驱动已结束）**：重试 2 次后 artifact_sha 变为 `dec55e0acb3e…`
  （worker 重提重建的包），第 3 次仍同错拒收，任务行进 **QUARANTINED（retry=3/3）**，无回流环；
  轮询驱动以 POLL_FAIL 退出。至此 Case A 首轮终态落定，与 §5.6 预测一致，无新信息。
- 本日志已同步枢纽 `state/eval-os-phase2/g5-eval-wire-log-20261111.md`。





## 6. run-02 执行（2026-11-11 14:42 CST 起，接上一任 ec099fb7，父席定夺：开新 run eval-run-20261111-02，A/B/C 全放 run-02）

### 6.1 门禁重过（14:43 CST）
- 新取回执：hubctl search twt eval-os-mvp-phase2，memory `231458ee-c6f8-44a4-b63e-01fff4c1acab`、kb `e0b09cdd-f38f-4021-94ce-fa0b5bcc37c8`，ts 2026-11-11T06:43:23Z。
- 本地 implementation-gate PASS（exit 0，--require-memory --require-kb --require-plan；plan 23085d4b 24h 内有效，至 2026-11-12 06:48 CST 过期）。
- 实查：eval_cases 33 行；eval-gate.py 部署 sha 2d7ac93e…、dispatch-gate-hub.py 2e600ef5… 与上一任回报一致。

### 6.2 Case A run-02 合同（修正形状）与 gate-4 复现
- run-02 合同由 run-01 合同程序化派生（仅 5 处改动）：task_id → eval-EVAL-001-20261111-02、eval_run_id → eval-run-20261111-02（顶层 + eval 内）、output_path → case-a/immersion_engine.py、outputs 去掉 2 个测试文件（只留 3 个源码文件）、workdir + dependencies worker_rel → -02。test_files、code_files 不变。
- 自写复现脚本 /tmp/run02-gate4-repro-20261111.py（新写，非复用旧驱动），直接调 verifier 的 _py_source_candidates + _gate4_redgreen：run-01 candidates 含 2 个测试文件 → reject "tests PASS on stub implementation (tests are vacuous)"（与生产失败同文）；run-02 candidates 仅 3 个源码文件 → pass "tests fail on stub, pass on real source"。REPRO_OK。
- 合同落盘 state/pipeline-formal-20261106/eval-EVAL-001-20261111-02-contract.json；taskcontract_gate --insert-guard：INSERT ALLOWED。

### 6.3 派发两次：第一次 403 的根因与修复（PLAN 文件权限）
- PLAN 写 state/eval-EVAL-001-20261111-02/PLAN-eval.md（八段齐全）；preflight --record：RECORDED。
- verdict 签发 2026-11-11T06:45:02Z：ceo-seat/state/butler-verdicts/eval-EVAL-001-20261111-02.json，contract_hash `2f00f881…` 与本地 canonical 重算一致；VERDICT_ISSUED 事件已写 eval_traces（trace_id=verdict:eval-run-20261111-02:EVAL-001）。
- 第一次 pipeline-dispatch：本地四门全过，但 gate-svc /gate/dispatch 403，journal `DISPATCH-REFUSED eval-EVAL-001-20261111-02 ['dispatch-gate-hub']`。
- 根因（实查，非推断）：dispatch-gate-ledger 显示服务端 run 为 `PLAN=MISS; CONTRACT=OK; VERDICT=OK; PREFLIGHT=OK`。PLAN 文件经 scp 落盘为 660 且 ACL other::---（run-01 的 PLAN 是 664 other::r--），gatesvc 用户读不到，plan-first-gate 报 "计划文件不可读：[Errno 13] Permission denied"。本地 ubuntu 跑全过，所以第一次本地门禁看不出。
- 修复（只改我方文件权限，未动共享配置/门禁/触发器）：chmod 644 该 PLAN 文件；以 gatesvc 身份、scripts/ CWD 精确复现服务端 run，四门全过 GATE_PASS exit 0。
- 教训：凡 scp 上枢纽、须 gatesvc 读的文件（PLAN 等），落盘后须 chmod 644 再派发；Case B/C 的 PLAN 同样处理。

### 6.4 Case A run-02 已派发（14:48 CST）
- 第二次 pipeline-dispatch：GATE_PASS；gate-svc receipt issued（mode=enforce，7 门全列）；任务行 UUID `216d33ca-e624-49a3-b9d7-0b53044cda66`，READY 入库。
- provision 5 文件，sha 与 run-01 一致（d107ded6…/6f5e9c19…/5077f58f…/75db3836…/009c7eb8…）。
- 轮询：自写 /tmp/run02-poll-a.py（nohup，PID 4137142，日志 state/eval-os-phase2/case-a/dispatch-20261111-02.log，30 分钟超时）；14:49 worker-1 已 CLAIMED。

### 6.5 Case B 干跑准备（Case A 轮询期间，不派发，只备料）
- QUEUE_API_REVIEW_ENFORCE=1：queue_api 进程 PID 2741214 环境实查在位；代码核对：/v1/submit 先调 _ensure_review（artifact_bundle.load_artifact 解包，篡改包报 artifact-extract-fail），再跑 code-review-gate-20261107.py，非 0 且 ENFORCE=1 即 409 review_blocked；queue_api stdout 在 state/logs/queue-api.log（干跑证据源）。
- 篡改包构造器 + 监视器已部署到枢纽 scripts/（此前只在本地）：eval-caseb-tamper-20261111.py、eval-caseb-watch-20261111.py。
- 篡改包已构造（隔离目录 state/eval-os-phase2/case-b/）：valid 11761B sha 8075dd24…、tampered 11766B sha 199cc145…；只改 ladder.py 一处 "Red!" → "RedX!"；parse_bundle 自验报 "bundle: sha mismatch for ladder.py"；原始 5 文件 sha 与派发 provision 一致。

### 6.6 Case A run-02 Gate 检查：通过（2026-11-11 14:53 CST）
- 终态：DONE，retry=0，artifact_sha `96e20da54f49b7ec7cf039517e154cafaecc08993145a6093f80bf822d4576e0`（11986B），轮询 POLL_OK。
- verifier verdict（state/verifier-verdicts/216d33ca-e624-49a3-b9d7-0b53044cda66.json）：五门全 PASS，ok=true。Gate 1 file/sha/bytes ok；Gate 2 ast ok, 16 non-empty defs；Gate 3 pytest exit 0 in fresh dir；Gate 4 stub run failed as expected (exit 2)——run-01 致命的 vacuous 缺陷已消除；Gate 5 not a pytest stub。
- Trace：eval_traces 有 VERDICT_ISSUED 行（event_id=2，trace_id=verdict:eval-run-20261111-02:EVAL-001，actor eval-gate）；central_events 12 行完整链（CREATED→READY→…→RUNNING→VERIFYING→DONE）；verdict 文件、dispatch receipt、provision receipt、verifier verdict 四处收据齐全，以（run-02，EVAL-001，task_slug，UUID，contract_hash）关联。
- 观测（不影响结论）：链中有两次 CLAIMED→READY 快速回弹（06:47:02、06:48:01，各 <1 秒），终态仍正常 DONE、retry=0；疑似认领端竞态，记录在案，不展开修。
- 局限（如实记，不掩盖）：无统一 trace_id——CREATED 事件 payload 为 {}，queue_api 未在 TASK_CREATED 时向 eval_traces 写真实 trace_id；M3 缺口仍在，Trace 靠（run，eval，task_slug）关联，不靠单一 trace_id。
- Baseline 快照：eval_baselines baseline_id=1，EVAL-001 version=1（ts 2026-11-11T06:53:53Z），metrics 含 status DONE、pytest 47、verifier 五门全 PASS、artifact sha/bytes、retry 0、created_to_done 195 秒；重查核实。
- 结论：Case A run-02 通过。按顺序进 Case B 干跑。

### 6.7 Case B 干跑：拦截链验证通过（2026-11-11 14:56–14:58 CST）
- 干跑任务：eval-EVAL-005-20261111-99（合成，run eval-run-20261111-99），行 UUID `85860a1a-6a13-4132-9665-b8bd43209774`；verdict 由签发代码产生（干跑 run，不占 run-02 名额）。
- 监视器：artifact_seen t=71.489s（worker 正常包 11986B，sha 1d0ca3fa…）→ swapped t=71.510s（21ms），ok=True；quarantine 与 deliverables 两处均由 1d0ca3fa… 换成篡改包 199cc145…；监视器随即退出 0。
- queue-api.log：`submit-review-producer … artifact-extract-fail:bundle: sha mismatch for ladder.py`；`submit-review … REVIEW-BLOCKED rc=1`。
- worker 侧 pull_once.log：`submit http=409 resp={'error': 'review_blocked', 'detail': 'REVIEW-BLOCKED … standards: no review record / spec: no review record'}`。
- 任务行：CLAIMED，retry=0，未进 VERIFYING——篡改包未到达 verifier，拦截发生在 review gate 层（与上一任实查的真实拦截点一致）。
- 结论：干跑通过。替换时序与 review gate 拦截均已实证，可以花真实 run-02 Case B verdict。干跑任务留 CLAIMED，租约过期后按正常路径重提合法包（预期修正重提后 DONE，合成任务，不计入任何 Case）。

## 7. 接手 run-02（2026-11-11 15:00 CST 起，本席 twt 新任，董事长令：run-02 接线员已由出席位停掉，从自己会话重派）

### 7.1 接手时实查（本席，未凭记忆）
- Case B 真实 verdict 已签（14:57:08Z，`eval-EVAL-005-20261111-02.json`，eval-通过，plan_id 23085d4b，contract_hash `4cb8cca7…`）；合同 `eval-EVAL-005-20261111-02-contract.json`（14:56）、provision 收据（14:57，5 文件 sha 与 run-01 一致）。
- 真实任务行 `65ac48a3-740a-456e-be0c-1281d523a67e`：CLAIMED，retry=0，updated 06:57:19Z；watch-02 日志 artifact_seen 11986B t=33.589s → swapped t=33.610s ok=True。
- 真实拦截已实证（本席查 queue-api.log）：`3171: submit-review-producer 65ac48a3… artifact-extract-fail:bundle: sha mismatch for ladder.py`；`3172: submit-review 65ac48a3… REVIEW-BLOCKED rc=1`。任务未进 VERIFYING，篡改包未到 verifier。
- 中央队列当时：total 1340、DONE 1241、QUAR 96、READY 1、CLAIMED 2（真实 + 干跑两 EVAL-005 任务）。
- KB 直查 run-02 Case B 未命中相关条目（只返回无关 grep 命中），故本节以 wire log + DB + 日志为准，不称“知识库已确认”。
- hubctl 本机 PATH 无，实为 `~/ai-team/scripts/hubctl`。

### 7.2 门禁重过（本席会话，15:00 CST）
- 新取回执：`hubctl search twt eval-os-mvp-phase2 "run-02 Case B EVAL-005 dispatch"`，memory `90202fc7-0d34-44db-a5d5-52de74e1fff2`、kb `b6db7376-4764-43fe-ae10-80215dcfae50`，ts 2026-11-11T06:59:38Z；注：results `files_matched=0`（与交接所记 /search 回执疑点同款，门禁只认回执存在）。
- 本地 implementation-gate：`~/workspace/tools/implementation-gate.sh --task eval-os-mvp-phase2 --require-memory --require-kb --require-plan`，GATE PASS（exit 0）。implementation-gate.sh 系本次从枢纽新拉取（本地 tools 原无此文件）。

### 7.3 Case B 真实轮询（本席启动）
- 轮询脚本由上一任 `/tmp/run02-poll-a.py` 派生（仅改 task_id 为 eval-EVAL-005-20261111-02），`/tmp/run02-poll-b.py`，nohup PID 4159773，日志 `state/eval-os-phase2/case-b/dispatch-20261111-02.log`，30 分钟超时；15:01 实查仍 CLAIMED retry=0。
- 预期终态（运行计划 §2.4）：租约过期后工人重提合法包，DONE 且最终 artifact_sha ≠ 篡改包 sha（199cc145…）。终态一到即做 Gate 检查、写 eval_failures，再进 Case C。
- eval_failures 表：to_regclass 存在（public.eval_failures），但 cq_timer 角色无 eval_cases SELECT 权限；写 eval_failures 时走管理通道（docker exec -u postgres），写前先查表结构，不猜字段。

### 7.4 Case C 备料（只备料，未派发；顺序一次一个，须等 Case B Gate 检查通过）
- 定义见 `state/eval-os-first-cases-20261110.md` §EVAL-003：code 任务，空 workdir，合同要求 pytest 全绿；成功标准 pytest exit 5 被识别为空 workdir（不得误判 worker 腿断），任务进 RETRY_WAIT/QUARANTINED，failure_class=F-EMPTY，不得 DONE；验证：pytest exit 5、pull_once.log 含 fail reason=pytest_exit_5、business_failure_log 类别 4（基建故障），直接原因“空 workdir，派发链缺代码生成步”；归因 SYSTEM，责任层派发链，建议“codegen 步先种代码+测试再派发”。
- 现状：pipeline-formal 下尚无 EVAL-003 合同文件，phase2 下无 case-c 目录；合同须在 Case B Gate 检查通过后新写。

### 7.5 Case B Gate 检查通过（2026-11-11 15:22–15:23 CST）
- 终态：DONE，retry=2，artifact_sha `3c84853f2a508b75b22fed1fe6a81e51fc5390ef9058de42be1113fa44785a44`（11986B），verified_by c2-verifier，updated 07:20:02Z；waiter 第 22/28 轮命中 DONE（poll-b 日志 POLL_OK）。
- 篡改包永不 DONE：终态 sha ≠ 篡改包 199cc145…。
- 拦截在 review gate：queue-api.log 3171 artifact-extract-fail sha mismatch for ladder.py；3172 REVIEW-BLOCKED rc=1；任务未进 VERIFYING（篡改包未到 verifier）；3200–3201 合法包重提 producer-rc=0 REVIEW-PASS rc=0。
- verifier 五门全 PASS，ok=true；Gate 4 stub run failed as expected（exit 2）。事件链：CREATED 06:57:19Z → … → DONE 07:22:03Z，15 行 STATUS_CHANGE（含成对重复写）。
- eval_failures failure_id=1（该表首行）：EVAL-005 / VALIDATION / 拦截链有效，建议保持 REVIEW_ENFORCE=1（docker exec -u postgres 写入并回查）。
- 局限（如实记）：worker 侧 pull_once.log 409 未在枢纽复核（日志在 worker-1 机上，不在枢纽；干跑时上一任引用过）。服务端证据齐全，Gate 检查通过。
- 结论：Case B 通过。按顺序进 Case C。

## 8. Case C 派发被拒，等董事长定夺（2026-11-11 15:25–15:30 CST，未派发）
- 备料完成：合同 `eval-EVAL-003-20261111-02-contract.json`（taskcontract insert-guard ALLOWED）、PLAN-eval.md（八段 + §3.2 书面确认故意不种代码）、preflight RECORDED、verdict eval-通过 15:25:50Z（contract_hash `cae88e3d…`，valid_until 2026-11-12 07:25:50Z）、implementation-gate PASS。
- 两次派发均被 gate-svc /gate/dispatch 403 拒收（fail closed，未入库）：
  - 15:25:56 failed=['decision-code-gate', 'dispatch-gate-hub']（journalctl 实查）。
  - 诊断：dispatch-gate-hub 的 PLAN=MISS 是权限问题——scp 生成的 PLAN 文件 other::---，gatesvc 读不到；合同文件已有 user:gatesvc:r--。已修：setfacl -m u:gatesvc:r-- PLAN 文件；15:26:43 手动复核 PASS。
  - 15:30:07 诊断重跑：failed=['decision-code-gate'] 仅剩其一，确认诊断。PLAN 权限问题已排除。
- decision-code-gate 失败根因：code 任务要求 code_files + evidence_files 非空；Case C 按设计空 workdir、零代码文件，必然失败。这是运行计划未预见的设计冲突（计划写于 2026-11-10，G4 豁免写于 2026-11-11）。
- 已排除的两条路（附证据，不走）：
  - task_type 改 eval：gate-svc G4 豁免只认 eval 类型，但 worker pull_once.py（现行 G5 版）按 ptype 分派，eval 走 else → fail unsupported_type_eval，永不跑 pytest，探针失效。须保持 code 类型。
  - 合同里虚构 code_files/evidence_files 指既有文件：门禁只验存在性，能过但属于绕门禁，违反董事长 2026-11-07“定案必须落地到代码”本意，不做。
- 唯一正路：董事长批一次性 gate override（gate_overrides 表：gate_id=decision-code-gate，approved_by=chairman，expires_at 短期；该表目前空，无先例）。豁免范围只限本次 EVAL-003 派发；任务本身永不 DONE（合同写死），override 不放行任何 DONE。
- 时限：二阶段批准 2026-11-12 06:48 CST 过期；override 批了才能赶在过期前派完（派发 + 重试耗尽约 20–30 分钟）。
- 另记：15:27 左右本机 VM 被换，/root/.ssh/config 丢失致 SSH 断（3128 复位，同 11-07 根因）；私钥未损（/home/hatch/.ssh/id_ed25519 在）；已重建 Include 行并验通。restore-ssh-arm-agent.sh 在 ~/workspace/tools/ 里不存在（TOOLS.md 称有，实无），本次直接写文件修复，待补脚本。

### 8.1 Case C 派发成功（2026-11-11 15:53:49 CST，董事长令本席自行拍板）
- 董事长 15:53 令本席自行拍板；本席决定批一次性 decision-code-gate override（gate_overrides 表当时空、无先例；reason 写明授权来源与范围，仅限本次 EVAL-003 派发，任务永不 DONE）。
- override 行 expires_at 08:23:42Z（30 分钟），派发成功后随即删除（表回 0 行，窗口约 2 分钟，只覆盖这一次派发）。
- 派发结果：DISPATCH gate-svc receipt issued mode=enforce，七门全过（含 decision-code-gate，overridden）；任务 ca9a7ed1-2633-4bcc-b0a0-c98fd50ea0b6 插入 READY，retry=0，max=3；PIPELINE_DISPATCH_OK no files to provision（workdir 按设计保持空）。
- 轮询：第一轮 28 分钟超时未见终态（CLAIMED retry=0 约 21 分钟 → RETRY_WAIT retry=1 → 16:20 再次 CLAIMED retry=2）；第二轮轮询接续，16:46 命中终态。

## 9. Case C Gate 检查：不通过（2026-11-11 16:46–16:52 CST 实查）
- 终态：QUARANTINED，retry=3=max，artifact_sha 空，永不 DONE。第二轮轮询第 24/28 轮命中。
- §3.4 五项逐项：
  1. pytest exit 5——未直接证实。worker 日志只到 fail post failed，无 fail reason 行；exit 5 为推断（空 workdir + fail 已触发），不作实证。
  2. pull_once.log 5 行——未齐。第三次认领的 worker 序列为 claimed → checkpoint step=1 → heartbeat → workdir_exists → fail post failed（wake-scheduler.log:55822）；第 5 行是 fail post failed 而非 fail reason 行；第 1–2 次认领的 worker 日志未在枢纽找到。
  3. central_events payload F-EMPTY——未通过。15 行事件：终态事件（08:45:02Z）payload={"reason": "retrywait-terminal TERMINAL_FAILED-max-retries", "failure_class": "F-UNDIAG"}，其余 14 行 payload 均为 {}。F1 盖戳未触发。
  4. business_failure_log 类别 4——已写（failure_id=16，is_fixed=false，retest_result 空）。
  5. retry_count ≤ max=3、无回流环——通过。链为 READY→CLAIMED→RETRY_WAIT ×3 → QUARANTINED，无 DISPATCHING 回流。
- 根因（新发现，非探针设计问题）：worker 第三次认领 08:20:05Z，workdir_exists 后 08:20:06Z 调 /v1/fail 上报失败原因时连接被关闭（Remote end closed connection without response），失败原因未落库；任务靠租约过期进 RETRY_WAIT，终态由 retrywait-terminal 盖 F-UNDIAG。queue-api.log 无该任务 fail 行，无重启/报错行可查；连接中断原因未确认，不猜。
- 归因口径：终态写 F-UNDIAG，未写成“worker-1 执行腿断”（这一点没错）；错在没写成 F-EMPTY。按 §3.7，Case C 不通过，进 Gate 3 复核。
- 双账本（docker exec -u postgres 写入并回查）：
  - eval_failures failure_id=2：EVAL-003 / SYSTEM / 派发链缺代码生成步，建议 codegen 步先种代码+测试再派发。
  - eval_failures failure_id=3：EVAL-003 / SYSTEM / 归因/分类器（F1 盖戳未触发），建议查 /v1/fail 连接中断原因、fail 上报失败须重试或本地留痕、修后重跑 EVAL-003 复测。
  - business_failure_log failure_id=16：类别 4，直接原因“空 workdir，派发链缺代码生成步”，is_fixed=false。
- 三考试总结：A 通过、B 通过、C 不通过（归因盖戳未触发）。C 的复测须先修 /v1/fail 上报链，再重跑；不得直接重派了事。
