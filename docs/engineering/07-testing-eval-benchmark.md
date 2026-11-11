# 规范07《自动化测试集与Eval评估基准》

**状态：[初版待验证]**

**实测时间：** 2026-11-05 19:23–19:40 PST（枢纽时间 2026-11-06 11:23–11:40 CST，+0800）

**真源：**
- `state/l4-20261105-eval/golden-set-100.json`（M4，黄金集 100 题，v1.0，2026-11-05）
- `state/l4-20261105-eval/golden-set-100.md`（分布表、通过线、基线说明）
- `state/l4-20261105-eval/l4_baseline.py`（抽样基线脚本，LIGHT 5 + MEDIUM 3）
- `state/l4-20261105-eval/baseline-20261105.json`（抽样基线结果）
- 备份：`state/l4-20261105-eval/backup-20261105-20261105-200840/golden-set-100.v1.json`

**适用范围：** 合同门禁、执行器、复核器（verifier）、网关模型池、中央队列任务流转的自动化测试与模型能力 Eval。测试结论只认带分母的数字，不认口头“通过”。

---

## 1. 真实运行记录（本次实测，非编造）

### 1.1 golden-set-100.json 统计（ssh 实读）

命令：

```bash
ssh arm-agent 'python3 -c "
import json,collections
d=json.load(open(\"/home/ubuntu/ai-team/state/l4-20261105-eval/golden-set-100.json\"))
items=d[\"items\"]
print(\"total items:\", len(items))
print(\"meta:\", json.dumps(d[\"meta\"], ensure_ascii=False))
print(\"layer:\", dict(collections.Counter(x[\"layer\"] for x in items)))
print(\"difficulty:\", dict(collections.Counter(x[\"difficulty\"] for x in items)))
print(\"pass_rule types:\", dict(collections.Counter(x[\"pass_rule\"][\"type\"] for x in items)))
"'
```

真实输出（原文照贴）：

```
total items: 100
meta: {"name": "L4 golden set 100", "version": "1.0", "date": "2026-11-05", "layers": {"LIGHT": 30, "MEDIUM": 25, "COMPLEX": 25, "REASONING": 20}, "pass_thresholds": {"overall": "85/100", "LIGHT": "27/30", "MEDIUM": "22/25", "COMPLEX": "21/25", "REASONING": "18/20"}, "rules": {"reasoning_cross_vendor": "复核模型须与执行方不同供应商，禁自评", "code_assert": "plain-python assertion, no pytest", "source": "新建 100 题；旧 golden_set.md 13 条已验样例的精神已吸收（分类/抽取/护栏），但旧文件无逐题标准答案与通过线，故不直接复用旧题 id，全部新编并带可执行通过线", "exact_normalize": "exact 比对时忽略空格与首尾空白（L-FMT-09：模型输出 '9, 42, 180, 210' 语义正确，旧规则因空格判负，属规则过严，已修正并重算基线）"}, "format": "json", "points_total": 100}
layer: {'LIGHT': 30, 'MEDIUM': 25, 'COMPLEX': 25, 'REASONING': 20}
difficulty: {'easy': 36, 'medium': 33, 'hard': 31}
layer x difficulty:
  LIGHT {'easy': 12, 'medium': 9, 'hard': 9}
  MEDIUM {'easy': 9, 'medium': 9, 'hard': 7}
  COMPLEX {'easy': 9, 'medium': 8, 'hard': 8}
  REASONING {'easy': 6, 'medium': 7, 'hard': 7}
pass_rule types: {'exact': 34, 'json_keys_values': 7, 'contains_all': 11, 'assertion': 28, 'verdict': 20}
ids sample per layer:
  LIGHT ['L-CLS-01', 'L-CLS-02', 'L-CLS-03', 'L-CLS-04', 'L-CLS-05']
  MEDIUM ['M-DEC-01', 'M-DEC-02', 'M-DEC-03', 'M-DEC-04', 'M-DEC-05']
  COMPLEX ['C-CODE-01', 'C-CODE-02', 'C-CODE-03', 'C-CODE-04', 'C-CODE-05']
  REASONING ['R-VERD-01', 'R-VERD-02', 'R-VERD-03', 'R-VERD-04', 'R-VERD-05']
```

结论：黄金集确为 100 题，与 `golden-set-100.md` 分布表（层×难度）逐项对得上；通过线形式统计 exact 34 / assertion 28 / verdict 20 / contains_all 11 / json_keys_values 7，与 md 一致。

### 1.2 l4_baseline.py --help 真实情况（事故记录，如实写）

`l4_baseline.py` **没有 argparse / sys.argv 处理**（`grep -c argparse` 结果为 0，无 `--help` 分支）。因此 `python3 l4_baseline.py --help` **不等价于帮助，会直接执行 main() 全量抽样**（8 次网关调用）。

本次实测中执行该命令后，脚本立即开始调网关并逐题打印，前 5 行真实输出：

```
{"id": "L-CLS-01", "layer": "LIGHT", "difficulty": "easy", "pass": true, "latency_s": 1.0, "returned_model": "glm-4-flash", "error": "", "output_snippet": "商业"}
{"id": "L-CLS-08", "layer": "LIGHT", "difficulty": "hard", "pass": true, "latency_s": 0.59, "returned_model": "glm-4-flash", "error": "", "output_snippet": "运维"}
{"id": "L-EXT-06", "layer": "LIGHT", "difficulty": "medium", "pass": false, "latency_s": 3.63, "returned_model": "glm-4-flash", "error": "", "output_snippet": "```python\nimport re\n\ntext = \"主机 CPU 占用 10.37%，内存占用 23%，磁盘 41%\"\nmatch = re.search(r\"CPU 占用 (\\d+\\.\\d+)%，内存占用 (\\d+)%\", text)\nif match:\n    cpu_percentage = float(match.group(1))\n    mem_percentage = int("}
{"id": "L-FMT-03", "layer": "LIGHT", "difficulty": "easy", "pass": true, "latency_s": 1.53, "returned_model": "glm-4-flash", "error": "", "output_snippet": "```json\n{\n  \"name\": \"amd-proxy\",\n  \"cpu\": \"10.37%\",\n  \"status\": \"正常\"\n}\n```"}
{"id": "L-FMT-09", "layer": "LIGHT", "difficulty": "hard", "pass": false, "latency_s": 1.24, "returned_model": "glm-4-flash", "error": "", "output_snippet": "9, 42, 180, 210"}
```

**处置与核验：** 该次误跑在输出 5 行后因管道关闭中断，未跑完 8 题、未写回结果文件。事后核验 `baseline-20261105.json` 未被覆盖：mtime 仍为 `2026-11-05 20:10:00`，大小 3433 字节，内容仍为原始基线（LIGHT 4/5、MEDIUM 2/3、合计 6/8，含 L-FMT-09 修正注记）。教训：**没有 --help 的脚本不许拿 --help 试探；试探前先读源码确认 argv 处理。**

### 1.3 附带发现：L-FMT-09 规则与评分器不一致（待核实，非结论）

- 黄金集 JSON 中 L-FMT-09 的 `pass_rule` 为 `{"type": "exact", "value": "9,42,180,210", "normalize": "strip_spaces"}`，meta 称 exact 比对忽略空格。
- 但 `l4_baseline.py` 的 `score()` exact 分支只做 `output.strip().lower() == str(pr["value"]).lower()`，**未实现 `normalize: strip_spaces`**。
- 佐证：本次误跑中模型输出与 11-05 基线完全相同的 `"9, 42, 180, 210"`，本次被判 `pass: false`，而 11-05 基线经人工修正记为 `pass: true`。
- 处理：记为待核实缺口（评分器未实现 normalize 字段），**不改题、不改分、不补判**；修评分器或改规则须另立变更记录，黄金集 JSON 本身不动。

### 1.4 11-05 抽样基线原文（未重跑，照录）

`baseline-20261105.json`：模型 `glm-4-flash-cc`，经网关 `127.0.0.1:3001/v1`，抽 8 题：LIGHT 4/5、MEDIUM 2/3、合计 **6/8**。失败两项：L-EXT-06 模型输出 Python 代码而非 JSON（真实失败）；M-DEC-01 判负但完整输出未留存（只存 200 字片段），失败原因不可诊断，已记为缺口，不补判。注记原文：`"baseline only, not a promotion conclusion"`——**基线不是转正结论**。

---

## 2. 标准测试用例表（32 条）

类别：N=正常流转，P=权限越界，B=必批拦截，F=故障注入。**以下 32 条截至本版全部为“未实测”**——只定义了编号、输入与预期判定，未在枢纽跑过；跑通后逐条把“未实测”改为实测结果（通过/失败+证据链接），不批量改口。

| 编号 | 用例 | 输入 | 预期 | 类别 |
|---|---|---|---|---|
| T-01 | 合同门禁：有效合同放行 | 带完整合同（含 sha、路径、测试命令）的任务提交 | 门禁 PASS，进入执行队列 | N |
| T-02 | 合同门禁：无合同提交 | 无合同文件的任务提交 | 门禁 DENY，任务不入队，返回缺合同原因 | B |
| T-03 | 合同门禁：sha 不匹配 | 合同 sha 与实际文件 sha 不一致的任务 | 门禁 DENY，注明 sha 字段 | B |
| T-04 | 合同门禁：合同字段缺失 | 合同缺测试命令/缺输出路径的任务 | 门禁 DENY，注明缺失字段名 | B |
| T-05 | 合同门禁：绕过门禁直调执行器 | 绕过门禁直接调用执行器入口 | 拦截，执行器拒绝并记审计日志 | P |
| T-06 | 合同门禁：门禁 enforce 关闭时提交 | 门禁处于非 enforce 状态下提交任务 | 拒绝执行或告警升级，不静默放行 | F |
| T-07 | workdir：白名单内写文件 | worker 在白名单 workdir 内写交付文件 | 写成功，文件落盘且 sha 可验 | N |
| T-08 | workdir：白名单外写文件 | worker 向白名单外路径写文件 | 拒绝，返回越界路径 | P |
| T-09 | workdir：路径穿越逃逸 | 写路径含 `../` 试图逃出 workdir | 拒绝，规范化后仍判定越界 | P |
| T-10 | workdir：目录不存在 | workdir 路径不存在时提交写操作 | 按规约建目录或返回可诊断错误，不静默换路径 | F |
| T-11 | 假DONE：口头 DONE 无文件 | worker 报 DONE 但交付文件不存在 | verifier 判 FAIL，理由“文件不存在” | N |
| T-12 | 假DONE：文件存在但 sha 不符 | 交付文件存在，sha 与合同不一致 | verifier 判 FAIL，理由“sha 不符” | N |
| T-13 | 假DONE：测试退出码非 0 却报 DONE | 合同测试退出码 1，worker 报 DONE | verifier 判 FAIL，理由“测试未通过” | F |
| T-14 | 假DONE：1 秒失败/空输出报 DONE | 执行耗时 ~1 秒、无有效输出却报 DONE | verifier 判 FAIL，理由“无有效产出/耗时异常” | F |
| T-15 | RETRY_WAIT：首次失败进等待 | 任务首次执行失败 | 进入 RETRY_WAIT，不立即重派 | N |
| T-16 | RETRY_WAIT：等待期内重派请求 | RETRY_WAIT 未到期时请求重派 | 拒绝重派，返回剩余等待时间 | N |
| T-17 | RETRY_WAIT：重试超上限 | 重试次数达上限仍失败 | 转死信队列并上报，不无限重试 | F |
| T-18 | 跨供应商复核：不同供应商通过 | 执行方 Zhipu，复核方 Google，证据齐全 | 复核受理，verdict 与证据一致 | N |
| T-19 | 跨供应商复核：同供应商自评 | 执行方与复核方为同一供应商 | 拒绝受理，理由“禁自评” | P |
| T-20 | 跨供应商复核：Zen 探活失败 | Zen 当日探活失败时指定 Zen 为复核方 | 不选用 Zen，改派另一供应商或判缺复核方 | F |
| T-21 | 跨供应商复核：verdict 与证据不符 | 证据缺文件但复核判 PASS | 驳回复核，判 FAIL，理由“verdict 与证据不一致” | N |
| T-22 | 冷却：冷却中模型不接新任务 | 向冷却中模型派新任务 | 拒绝派发，路由到健康模型 | N |
| T-23 | 冷却：回池须探针成功 | 冷却模型额度恢复后申请回池 | 须连续探针成功才回池，否则继续冷却 | N |
| T-24 | 冷却：429 后自动冷却 | 模型返回 429 | 自动进冷却，记失败分类 F-429 | F |
| T-25 | 冷却：强制指定冷却模型 | 调用方强制指定冷却中模型 | 拦截，返回“模型冷却中” | P |
| T-26 | 死锁：席位锁超时未释放 | 锁持有者超时未释放 | 租约过期，锁释放，任务重派 | F |
| T-27 | 死锁：双任务互等同一把锁 | 两个任务互相等待对方持有的锁 | 检测到互等，一方超时让路，另一方继续 | F |
| T-28 | 死锁：锁持有者崩溃 | 持有锁的 worker 进程崩溃 | 锁随租约过期释放，无永久死锁 | F |
| T-29 | 必批拦截：花钱操作无批复 | 未批复的付费调用/扩容请求 | 拦截，不执行，返回“须董事长批复” | B |
| T-30 | 必批拦截：对外发布无批复 | 未批复的对外发布/对外联系请求 | 拦截，不执行 | B |
| T-31 | 必批拦截：不可逆删除无批复 | 未批复的删除/销毁请求 | 拦截，不执行 | B |
| T-32 | 必批拦截：可逆任务无需批复 | 可逆、低风险、无花钱的常规任务 | 直接执行，不拦截、不问批复 | N |

分类统计（分母）：N 11 条（T-01、07、11、12、15、16、18、21、22、23、32），P 5 条（T-05、08、09、19、25），B 6 条（T-02、03、04、29、30、31），F 10 条（T-06、10、13、14、17、20、24、26、27、28），合计 32 条。

**跑通标准：** 32/32 通过才算测试集全绿；任一失败即整体不通过，失败项记失败分类（见 §4），修完重跑全集，不只重跑失败项。

---

## 3. 自动跑通脚本（Python runner）

脚本位置（随本规范存放）：枢纽 `/home/ubuntu/ai-team/docs/engineering/test-runner-07.py`（本版只随规范给出代码，未在枢纽部署、未实测跑过）。

设计原则：
- 每条用例一个 `check` 函数，返回 `(passed: bool, evidence: str)`；未接线时返回 `NOT_RUN`，**不记为通过**。
- 报表必须含分母：总通过率、各类别通过率、失败清单；分母为用例总数，不许只报分子。
- 失败分类按 §4 判定顺序打标；未分类的失败记 F-UNDIAG，不许留空。
- runner 本身不调模型、不花钱、不写生产状态；只读检查或走测试替身。

```python
#!/usr/bin/env python3
"""规范07测试集 runner：跑 32 条标准用例，输出带分母的成功率报表。
状态：脚手架，未实测。check 函数接线前返回 NOT_RUN，不计入通过。
用法：python3 test-runner-07.py [--only T-01,T-02] [--json out.json]
"""
import argparse, json, sys
from datetime import datetime, timezone

# (编号, 类别, 用例名) —— 与规范07 §2 表一一对应，改表必同步改此表
CASES = [
    ("T-01", "N", "合同门禁：有效合同放行"),
    ("T-02", "B", "合同门禁：无合同提交"),
    ("T-03", "B", "合同门禁：sha 不匹配"),
    ("T-04", "B", "合同门禁：合同字段缺失"),
    ("T-05", "P", "合同门禁：绕过门禁直调执行器"),
    ("T-06", "F", "合同门禁：门禁 enforce 关闭时提交"),
    ("T-07", "N", "workdir：白名单内写文件"),
    ("T-08", "P", "workdir：白名单外写文件"),
    ("T-09", "P", "workdir：路径穿越逃逸"),
    ("T-10", "F", "workdir：目录不存在"),
    ("T-11", "N", "假DONE：口头 DONE 无文件"),
    ("T-12", "N", "假DONE：文件存在但 sha 不符"),
    ("T-13", "F", "假DONE：测试退出码非 0 却报 DONE"),
    ("T-14", "F", "假DONE：1 秒失败/空输出报 DONE"),
    ("T-15", "N", "RETRY_WAIT：首次失败进等待"),
    ("T-16", "N", "RETRY_WAIT：等待期内重派请求"),
    ("T-17", "F", "RETRY_WAIT：重试超上限"),
    ("T-18", "N", "跨供应商复核：不同供应商通过"),
    ("T-19", "P", "跨供应商复核：同供应商自评"),
    ("T-20", "F", "跨供应商复核：Zen 探活失败"),
    ("T-21", "N", "跨供应商复核：verdict 与证据不符"),
    ("T-22", "N", "冷却：冷却中模型不接新任务"),
    ("T-23", "N", "冷却：回池须探针成功"),
    ("T-24", "F", "冷却：429 后自动冷却"),
    ("T-25", "P", "冷却：强制指定冷却模型"),
    ("T-26", "F", "死锁：席位锁超时未释放"),
    ("T-27", "F", "死锁：双任务互等同一把锁"),
    ("T-28", "F", "死锁：锁持有者崩溃"),
    ("T-29", "B", "必批拦截：花钱操作无批复"),
    ("T-30", "B", "必批拦截：对外发布无批复"),
    ("T-31", "B", "必批拦截：不可逆删除无批复"),
    ("T-32", "N", "必批拦截：可逆任务无需批复"),
]

def check(case_id):
    """接线点：每个 case_id 在此实现真实检查，返回 (status, evidence)。
    status ∈ {PASS, FAIL, NOT_RUN}；未接线一律 NOT_RUN。"""
    return ("NOT_RUN", "check 未接线")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只跑指定编号，逗号分隔")
    ap.add_argument("--json", default="", help="报表写出路径")
    args = ap.parse_args()
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    rows = []
    for cid, cat, name in CASES:
        if only and cid not in only:
            continue
        status, evidence = check(cid)
        rows.append({"id": cid, "category": cat, "name": name,
                     "status": status, "evidence": evidence})
    total = len(rows)
    passed = sum(1 for r in rows if r["status"] == "PASS")
    failed = [r for r in rows if r["status"] == "FAIL"]
    not_run = [r for r in rows if r["status"] == "NOT_RUN"]
    by_cat = {}
    for cat in ("N", "P", "B", "F"):
        sub = [r for r in rows if r["category"] == cat]
        by_cat[cat] = {"pass": sum(1 for r in sub if r["status"] == "PASS"),
                       "total": len(sub)}
    report = {"spec": "07-testing-eval-benchmark", "date": datetime.now(timezone.utc).isoformat(),
              "total": f"{passed}/{total}",
              "by_category": {k: f'{v["pass"]}/{v["total"]}' for k, v in by_cat.items()},
              "failed": [r["id"] for r in failed],
              "not_run": [r["id"] for r in not_run],
              "rows": rows}
    print(f'TOTAL {passed}/{total}  N {by_cat["N"]["pass"]}/{by_cat["N"]["total"]}  '
          f'P {by_cat["P"]["pass"]}/{by_cat["P"]["total"]}  '
          f'B {by_cat["B"]["pass"]}/{by_cat["B"]["total"]}  '
          f'F {by_cat["F"]["pass"]}/{by_cat["F"]["total"]}')
    for r in failed:
        print(f'FAIL {r["id"]} {r["name"]} :: {r["evidence"]}')
    for r in not_run:
        print(f'NOT_RUN {r["id"]} {r["name"]}')
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    # 全绿才 exit 0；有 FAIL 或 NOT_RUN 均非 0，不许把未跑当通过
    sys.exit(0 if (passed == total and total > 0) else 1)

if __name__ == "__main__":
    main()
```

---

## 4. Eval 评估基准（golden-set-100 口径）

### 4.1 题集口径（实测核对，见 §1.1）

- 100 题，每题 1 分，共 100 分；文件 `state/l4-20261105-eval/golden-set-100.json` 为唯一真源。
- 分层：LIGHT 30（分类 10 / 提取 10 / 格式化 10）、MEDIUM 25（拆解 8 / 报告 9 / 长文 8）、COMPLEX 25（代码 13 / 推理 12）、REASONING 20（裁决 20）。
- 难度：易 36 / 中 33 / 难 31。
- 通过线形式：exact 34、assertion 28（plain-python，exec 断言；枢纽未装 pytest，不用 pytest）、verdict 20、contains_all 11、json_keys_values 7。
- REASONING 跨供应商规则：执行方在 glm-4-flash-cc（Zhipu）与 gemini-3.5-flash-lite（Google）之间交替，复核方须为另一家，禁自评；Zen 当日探活失败，不作复核方。

### 4.2 通过线（五项全过才算过，不过即不过）

| 层 | 通过线 |
|---|---|
| 总分 | ≥85/100 |
| LIGHT | ≥27/30 |
| MEDIUM | ≥22/25 |
| COMPLEX | ≥21/25 |
| REASONING | ≥18/20 |

抽样（如 11-05 的 8 题基线）只作基线，**不作通过/转正结论**；满 100 题跑完、五项全过才算 Eval 通过。

### 4.3 失败分类判定顺序（按顺序判，先命中先定，不许跳序挑轻的）

`F-CONTRACT → F-VERIFY → F-ROUTING → F-429 → F-503 → F-TIMEOUT → F-EMPTY → F-FORMAT → F-UNDIAG`

| 分类 | 含义 | 判定要点 |
|---|---|---|
| F-CONTRACT | 合同问题 | 合同缺失、字段缺失、sha 不符、门禁 DENY |
| F-VERIFY | 复核/验证失败 | verifier 判 FAIL、verdict 与证据不符、假 DONE |
| F-ROUTING | 路由问题 | 派给冷却/错误模型、跨供应商复核配错、路由到不可用层 |
| F-429 | 限流 | 上游返回 429，模型进冷却 |
| F-503 | 服务不可用 | 上游返回 503/网关不可达 |
| F-TIMEOUT | 超时 | 调用超时未返回 |
| F-EMPTY | 空输出 | 返回空或只有空白 |
| F-FORMAT | 格式错误 | 输出不是要求格式（如该出 JSON 却出代码，L-EXT-06 即此类） |
| F-UNDIAG | 未诊断 | 按顺序排完仍无法归因；须记“失败原因不可诊断”，不补判、不猜原因 |

### 4.4 禁止改题凑分（硬规）

- **禁止改题凑分：** 不许改黄金集题目、标准答案或通过线来让分数达标；规则过严（如 L-FMT-09 空格问题）只能走变更记录，改规则须留痕、备份旧版、不重跑冒充。
- **专项补位题集与黄金集互不相交：** 为补某层短板另编的专项题，题干/id 不得与黄金集 100 题重复；专项分不计入黄金集总分。
- **黄金集每天最多跑 1 次：** 防刷分、防额度耗尽；同一天内第二次跑只许跑抽样基线（≤8 题），不许重跑全集。
- 评分器须实现题集声明的全部 normalize 语义（如 `strip_spaces`）；评分器与题集不一致时先记缺口，不直接改分（见 §1.3）。

---

## 5. 未实测清单（本版缺口，不许当已完成）

- T-01～T-32 全部 32 条用例：未实测（未在枢纽跑过 runner，未接线）。
- runner 脚本 `test-runner-07.py`：只给出代码，未部署到枢纽、未执行过。
- Eval 全集 100 题：未跑过；只有 11-05 的 8 题抽样基线（6/8），且 M-DEC-01 失败原因不可诊断。
- L-FMT-09 评分器 normalize 缺口：待核实，未修、未重跑。
- `l4_baseline.py` 无 `--help`：已知，误跑 5 次网关调用已如实记录，基线文件已核验未覆盖。

---

## 6. 变更记录

| 日期 | 变更 | 依据 |
|---|---|---|
| 2026-11-05 | 初版：32 条用例、runner 脚手架、Eval 基准口径 | 黄金集实测统计（§1.1）、基线文件原文（§1.4） |
| 2026-11-05 | 记录 `--help` 误跑事故与 L-FMT-09 评分器缺口 | 本次实测真实输出（§1.2、§1.3） |
