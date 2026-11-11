# 规范04《模型网关路由与降级策略》

**状态：[初版待验证]**

- 实测时间：2026-11-06 11:24 CST（枢纽 `date` 实测；对应 2026-11-05 19:24 PST）
- 真源（枢纽 `/home/ubuntu/ai-team/` 下）：
  - `state/gateway-pool.json`（文件 mtime 2026-11-06 00:02；内 `date` 字段 2026-11-05，`status` 为 `final-round2`）
  - `state/model-pool-handbook-20261105/HANDBOOK.md`（v1.0，2026-11-05 23:30 CST）
  - `state/l4-20261105-eval/`（`golden-set-100.json`、`golden-set-100.md`、`baseline-20261105.json`、`l4_baseline.py`）
  - `state/gateway-layers-20261105/EVIDENCE-20261105.md`（四层落地证据，2026-11-05）
  - `scripts/model-health.py`（`route` / `route-layer` / `revive` 实现）、`scripts/review-supplier-check.py`（Order 15 跨供应商检查）
  - `state/config-history/gateway-channels.json`（newapi 渠道快照，文件 mtime 2026-11-05 06:22，共 13 行）
- 口径：本规范只写实查到的数与实查到的文件内容。写“待核实”就是没查到，不编。Key 只记后 4 位，完整 Key 永不进本规范。

## 1. 生产底座与池子现状（实测）

生产底座定死 **2+1**：网关 2 个模型 + Zen 1 腿，不人工直派测试池模型。

| 项 | 实测值 |
|---|---|
| `tier_a` / `tier_1` | `glm-4-flash-cc`、`qwen3.8-flash` |
| `tier_b` / `tier_1_backup` | `[]`（空） |
| `pool_count` | 2 |
| `primary` | `glm-4-flash-cc` |
| `suppliers` | `Zhipu`、`Alibaba-Bailian` |
| `pool_minimum_2_met` | `true`（下限：≥2 个不同供应商 + Zen 作备用金丝雀，Order 14 B6） |
| `pool_minimum_3_met` | `false`（旧 3 模型下限已由 Order 14 B6 在池定义层取代，不再作数） |
| `production_base.gateway_2` | `glm-4-flash-cc`、`qwen3.8-flash` |
| `production_base.plus_1` | Zen（DeepSeek，`opencode/deepseek-v4.1-flash-free`），COMPLEX 代码层主力/备用腿，不计入 `pool_count`，不进 `tier_a`/`tier_b` |
| Zen 腿现状 | 仍不可用：2026-11-05 16:58 探活 180 秒超时 FAILED；回 B 档条件（连续 3 次探针成功，间隔 ≥10 分钟 + 1 个金丝雀）未满足 |

入池/出池标准（Q1，CHAIR-20261105-17 2d-e，2026-11-05 生效，仅面向未来，不追溯重判旧轮次）：入池 = 连续 3 轮通过、轮间 ≥10 分钟；出池 = 连续 3 轮失败、跨 ≥30 分钟；单轮失败只计数 + 冷却，不改入池/出池。

## 2. 四层分层表

过线标准三件套（全层通用，缺一不可）：

1. **S2**：平均 ≥85 且单题 ≥60（与千问同款题目/rubric；LIGHT/MEDIUM/COMPLEX 各有自己的 S2 矩阵）。
2. **金丝雀**：轮间 ≥10 分钟，每轮 3/3，连续 3 轮全过才转正。
3. **L4 黄金集**：100 题总分 ≥85/100，分层线见下表；L4 通过线形式：exact 34、assertion 28、verdict 20、contains_all 11、json_keys_values 7。

另有硬门：`capabilities` 为空的模型自动打标 UNTESTED，生产路由默认排除；测试流量须显式 `--include-untested` / `--test`。转正流程：首探 200 → S2 通过 → 金丝雀 3×3/3 → 跨供应商复核 → 董事长批 → 进主池。

| 层 | 用途 | 主力（生产可发） | 备选 / Fallback | 过线标准 | 现状（实测） |
|---|---|---|---|---|---|
| LIGHT 轻量层 | 分类、提取、格式化、路由 | `glm-4-flash-cc`（智谱）+ `qwen3.8-flash`（百炼），5:5 轮询（`rr-state.json` 指针） | 主力 429/超时 → `agnes-3.0-flash`；再失败 → 另一主力；仍失败 → 记录失败，**不自动升级到 MEDIUM** | S2 平均 ≥85 且单题 ≥60；金丝雀连续 3 轮 3/3；L4 LIGHT ≥27/30 | 主力池实测全绿：`qwen3.8-flash` 于 2026-11-05 22:27 由测试池晋升（S2 通过 + 金丝雀 3×3 通过）；`glm-4-flash-cc` L4 基线 LIGHT 4/5。测试池：`agnes-3.0-flash`、`glm-4.7-flash`、`cf-glm-4.7-flash`、`cf-gemma-4-26b`、`Qwen/Qwen3.5-9B`、`THUDM/GLM-4-9B-0414`、`Agents-A1`、`Sage-A1.5`。`gemini-3.8-flash-lite` 已移出（网关 404，模型 id 不存在）；`gemini-3.5-flash-lite` 已出池（Order 17，见 §3） |
| MEDIUM 中等层 | 大管家拆解、常规报告、长文处理 | `qwen3.8-27b`（百炼，唯一主力，2026-11-05 22:27 晋升） | 429/503/超时 10 秒 → 无缝降级 `glm-4-flash-cc`；监工打回判太简单/格式错 → 下次重试强制升级 `gemini-3-flash-preview` 或更高层级 | S2 平均 ≥85 且单题 ≥60；金丝雀连续 3 轮 3/3；L4 MEDIUM ≥22/25 | `gemini-3-flash-preview`、`gemini-3.5-flash`、`gemini-3.6-flash` S2 两轮均为 **0/3**（preview 两轮全 429；3.5-flash 回包仅 11–35 字；3.6-flash 回包 18–56 字），本周无转正。`butler_split`（`gemini-3.5-flash` 0.7 / `glm-4-flash-cc` 0.3）已失效——70% 腿 S2 0/3，生产须走 `glm-4-flash-cc`。`qwen3.8-27b` 到期 **2026-11-18（最急）**，剩 994,789 Token（2026-11-05 22:26 守卫读数，以控制台为准），到期作废、不结转、同一实名领不到第二次 |
| COMPLEX 代码/重推理层 | 写代码、复杂诊断、架构权衡 | Zen（DeepSeek，`opencode/deepseek-v4.1-flash-free`），**只绑代码工人 worker-1**；席位默认脑仍为网关（令 13：网关为主、Zen 为备用），本层 Zen 优先仅限代码任务，不改席位默认脑 | Zen 超时/429/IP 限流 → 立刻落网关兜底，**不硬重试**：先 `nvidia/nemotron-3-ultra-550b-a55b:free`，再 `llama-4-scout`；代码跑不出则能力标签为虚，踢回冷却并记录实测 | S2 平均 ≥85 且单题 ≥60；金丝雀连续 3 轮 3/3；L4 COMPLEX ≥21/25 | Zen 当日不可用（§1）。兜底双模型 2026-11-05 代码实测均通过：生成 `solution.py`（add/mul），本地 plain-python 断言 `add(2,3)==5`、`add(-1,1)==0`、`mul(3,4)==12` 全过（枢纽未装 pytest，改用 plain-python 断言）。测试池：`llama-4-scout`、`qwen3.8-max-0902`（S2 66.7 未过，冷却至 2026-11-06 22:03:56）、`XingChenAGI/Xing4.0-29B`（S2 73.3 未过）、`Atria-Dawn`（S2 66.7/40.0 未过） |
| REASONING 复核层 | 交叉验证、独立审核 | **无专属模型，是规则层，不是模型池** | 见 §3 跨供应商强制绑定；无可用异供应商复核方时挂起等待，不降格 | L4 REASONING ≥18/20（裁决 JSON `{"verdict":"PASS"/"FAIL","reasons":[...]}`，须与标准一致且 reasons 命中 ≥min_keywords 个关键理由词） | 规则已写入 `gateway-pool.json`；复核走 `route-layer REASONING`，由调用方传入 producer 供应商。当前 Google 侧复核方受限，见 §3 |

L4 基线（`state/l4-20261105-eval/baseline-20261105.json`，2026-11-05，`glm-4-flash-cc` 经网关 `127.0.0.1:3001/v1`）：LIGHT 4/5、MEDIUM 2/3，总分 **6/8**。文件自注 `baseline only, not a promotion conclusion`——基线不是转正结论。

### 本节发现的文件内冲突（照实记，不弥合）

1. `layers.MEDIUM.status` 仍写 `test_pool 已建, S2 未测 (2026-11-05 待跑)`，与 `EVIDENCE-20261105.md` §5 及手册总表记录的“S2 两轮 0/3”矛盾——以后者实测为准，`status` 文本过期未更新。
2. `layers.MEDIUM.note` 仍写 `tier_b 主池仍为空……此处为测试池，未转正`，与同文件 `primary_pool: ["qwen3.8-27b"]`（22:27 已晋升）矛盾——以后者为准。
3. `layers.MEDIUM.butler_split` 仍写 `gemini-3.5-flash: 0.7`，与 S2 0/3 矛盾——该分流不可执行，见上表。

## 3. 跨供应商独立复核：强制绑定

铁律（`gateway-pool.json` `layers.REASONING.rule` 原文口径）：执行用智谱（`glm-4-flash-cc`）→ 复核强制 Google 系；执行用 Google 系 → 复核强制智谱（`glm-4-flash-cc`）；`gemini-3.5-flash-lite` 已出池，不再作复核方。当前 Google 侧唯一指定复核方为 `gemini-3-flash-preview`。

**禁止同供应商自评**：复核模型供应商必须与执行模型供应商不同；同供应商、供应商未知（UNKNOWN）一律 fail closed（拒绝执行复核，不静默降级）。

### 3.1 路由函数（规范代码，与枢纽现有实现对齐）

供应商映射以枢纽 `scripts/model-health.py` 的 `SUPPLIER` 表为准（该表含 `qwen3.8-* → Alibaba`）；检查脚本以 `scripts/review-supplier-check.py` 为准。两者不一致处见 §3.3。

```python
#!/usr/bin/env python3
"""规范04 §3：跨供应商独立复核强制绑定。

规则来源：枢纽 state/gateway-pool.json layers.REASONING.rule
          + review_policy.cross_supplier + scripts/review-supplier-check.py。
fail closed：供应商未知或同供应商，一律抛错，不降格、不自评。
"""
import subprocess
import sys

# 与 scripts/model-health.py SUPPLIER 表一致（含 qwen -> Alibaba）。
SUPPLIER = {
    "glm-4-flash-cc": "Zhipu", "glm-4-flash": "Zhipu",
    "glm-4.7-flash": "Zhipu", "probe-zhipu-flash": "Zhipu",
    "gemini-3-flash-preview": "Google", "gemini-3.5-flash": "Google",
    "gemini-3.6-flash": "Google", "gemini-3.8-flash": "Google",
    "gemini-3.5-flash-lite": "Google",  # 已出池（Order 17），仅留作映射，不可选中
    "gemini-3.8-flash-lite": "Google",  # 网关 404 死 id，不可选中
    "agnes-3.0-flash": "Agnes",
    "qwen3.8-27b": "Alibaba", "qwen3.8-flash": "Alibaba",
    "qwen3.8-max-0902": "Alibaba",
    "nvidia/nemotron-3-ultra-550b-a55b:free": "OpenRouter",
    "llama-4-scout": "Cloudflare",
    "Atria-Dawn": "Atria", "intern-s2": "Atria",
    "Agents-A1": "Atria", "Sage-A1.5": "Atria",
}

# 执行供应商 -> 复核候选（按优先顺序）。只列当前文件+实测承认的候选。
REVIEWER_CANDIDATES = {
    "Zhipu": ["gemini-3-flash-preview"],          # Google 系唯一指定复核方
    "Google": ["glm-4-flash-cc"],                  # 智谱系
    "Alibaba": ["glm-4-flash-cc", "gemini-3-flash-preview"],
    "OpenRouter": ["glm-4-flash-cc"],
    "Cloudflare": ["glm-4-flash-cc"],
    "Atria": ["glm-4-flash-cc"],
}

# 出池 / 死 id / 未转正：即使在映射表里，也绝不选中。
BLOCKED = {
    "gemini-3.5-flash-lite",  # OUT_OF_POOL，Order 17
    "gemini-3.8-flash-lite",  # 网关 404，模型 id 不存在
    "reviewer-primary",       # 幽灵别名，2026-11-05 已从 model-health 删除
}

def supplier_of(model_id: str) -> str:
    return SUPPLIER.get(model_id, "UNKNOWN")

def select_reviewer(producer_model: str, healthy_models: set) -> str:
    """返回复核模型 id。无可用异供应商复核方时抛错（挂起等待），绝不返回同供应商模型。"""
    producer_supplier = supplier_of(producer_model)
    if producer_supplier == "UNKNOWN":
        raise ValueError(f"producer supplier UNKNOWN: {producer_model}，拒绝复核")
    for cand in REVIEWER_CANDIDATES.get(producer_supplier, []):
        if cand in BLOCKED:
            continue
        if supplier_of(cand) == producer_supplier:
            continue  # 同供应商自评，禁止
        if cand not in healthy_models:
            continue  # 不健康 / 冷却中 / caps 为空（UNTESTED），跳过
        return cand
    raise RuntimeError(
        f"no healthy cross-supplier reviewer for producer={producer_model} "
        f"(supplier={producer_supplier})，复核挂起等待，不降格自评"
    )

def check_with_hub_script(producer_model: str, reviewer_model: str) -> bool:
    """调枢纽既有检查脚本 scripts/review-supplier-check.py 做二次核验。
    该脚本 exit 0 = 异供应商通过；exit 1 = 同供应商或未知映射。"""
    r = subprocess.run(
        [sys.executable, "scripts/review-supplier-check.py",
         producer_model, reviewer_model],
        capture_output=True, text=True)
    return r.returncode == 0
```

调用示例（执行 `glm-4-flash-cc` → 复核 `gemini-3-flash-preview`；执行 `qwen3.8-27b` → 复核 `glm-4-flash-cc`）：

```python
reviewer = select_reviewer("glm-4-flash-cc", healthy_models={"gemini-3-flash-preview", "glm-4-flash-cc"})
assert supplier_of(reviewer) != supplier_of("glm-4-flash-cc")
assert check_with_hub_script("glm-4-flash-cc", reviewer)
```

### 3.2 当前复核可用性（属实写：受限）

`gemini-3.5-flash-lite` 出池后，Google 侧复核方只剩 `gemini-3-flash-preview` 一个指定候选，而其实测状态是：

- S2 两轮 **0/3**（两轮全 429）；
- `model-health.json` 中 unhealthy，`cooldown_until` 至 2026-11-05 23:00:02；
- `gemini-3.5-flash`、`gemini-3.6-flash` 同样 S2 0/3 且 `capabilities` 为空，生产路由 UNTESTED 拦截，**不能**拿来顶复核位。

结论：**当前智谱执行（`glm-4-flash-cc`）的任务没有可用的健康 Google 复核方**。按 fail closed，复核挂起等待 `gemini-3-flash-preview` 冷却结束 + revive 重测通过，不许降格为智谱自评（`glm-4-flash-cc` 复核 `glm-4-flash-cc`）、不许用 caps 为空的模型复核、不许用已出池的 `gemini-3.5-flash-lite`。反向（Google 执行 → 智谱 `glm-4-flash-cc` 复核）目前可用，因 `glm-4-flash-cc` 健康且 caps 非空。

### 3.3 已发现的缺口（待修，不冒充已闭合）

1. **`scripts/review-supplier-check.py` 的 `supplier()` 没有 `qwen` / Alibaba 分支**：`qwen3.8-flash`、`qwen3.8-27b`、`qwen3.8-max-0902` 均落到 `UNKNOWN` → 检查脚本 exit 1 fail closed。百炼模型作执行方时，该脚本目前无法完成核验，须先给脚本补 Alibaba 映射（与 `model-health.py` 的 `SUPPLIER` 表对齐），补完之前以 §3.1 的 `SUPPLIER` 表 + 人工核对为准。
2. **`scripts/model-health.py` 的 `cmd_route_layer` 中 REASONING 的 `pool_list` 硬编码含已出池模型**：`["gemini-3.5-flash-lite", "gemini-3-flash-preview", "glm-4-flash-cc"]`，其中 `gemini-3.5-flash-lite` 已于 Order 17 出池。须从该列表删除，只留 `gemini-3-flash-preview` 与 `glm-4-flash-cc`；在修好之前，`alive()` 能否拦住它取决于 `model-health.json` 是否仍标它 healthy——本规范不替代码背书，修完须重测 `route-layer REASONING`。
3. **幽灵别名残留**：`reviewer-primary` 已于 2026-11-05 从 `model-health.json` 删除，`route --capability simple_query` 不再返回它；但 newapi 渠道 3/4/5/8 的 `models` 字段里仍有同名别名（见 §4 渠道表），live `/models` 仍列出它。清理须改 MySQL 4 个渠道行并验证 newapi 重载，属控制面改动，本次未做，另行定案；路由层已无法选中它，主流程不卡。

## 4. 网关配置：现有 newapi 渠道格式（照实写）

### 4.1 现行网关（实测）

- 网关：newapi，本地入口 `http://127.0.0.1:3001/v1`（`scripts/model-health.py` 的 `GATEWAY` 常量；`scripts/route.py` 的 `NEWAPI_BASE` 默认值同为该地址）。
- 调用方式：OpenAI 兼容 `/v1/chat/completions`，`Authorization: Bearer <NEWAPI_KEY>`，`model` 字段填网关模型 id（如 `glm-4-flash-cc`）。
- 渠道数据存在 MySQL（EVIDENCE 称库名 `ai-stack-new-api-db-1`）的渠道表；本规范的渠道表来自快照文件 `state/config-history/gateway-channels.json`（2026-11-05 06:22，共 13 行），**不是**实时库查询，快照之后的新建渠道不在其中。

### 4.2 渠道行格式（快照原文，一字不改的字段）

每行字段：`id`、`name`、`type`、`status`、`priority`、`weight`、`group`、`models`（逗号分隔的模型 id 列表）。`status`：`1` = 启用，`2` = 停用（渠道 9 `deepseek-ds2api` 为 `2`，赠品通道，只记 DOWN 静默）。`type` 观察值：`1` = OpenAI 兼容，`14` = Claude format，`16` = Zhipu GLM，`24` = google-gemini（type 24 的准确含义未在本轮核实，不编）。

| id | name | type | status | priority | models（快照原文） |
|---|---|---|---|---|---|
| 1 | Cloudflare Workers AI | 1 | 1 | 10 | `llama-4-scout,probe-cf-scout,free-auto,executor-primary` |
| 2 | Atria ASI | 1 | 1 | 10 | `Atria-Dawn-Preview`（404 死 id，未从 newapi 删，不得再用，改用 `Atria-Dawn`） |
| 3 | CF Workers AI (OpenAI compat) | 1 | 1 | 20 | `gpt-oss-120b,probe-cf-oss,reviewer-primary,foreman-prime`（含幽灵别名残留，见 §3.3） |
| 4 | Zhipu GLM | 16 | 1 | 5 | `glm-4-flash,scheduler-stable,verifier-stable,reviewer-primary`（含幽灵别名残留） |
| 5 | Zhipu GLM (Claude format) | 14 | 1 | 5 | `glm-4-flash-cc,probe-zhipu-flash,free-auto,reviewer-primary`（含幽灵别名残留） |
| 6 | Agnes AI | 1 | 1 | 0 | `agnes-3.0-flash,agnes-2.5-flash` |
| 7 | Agnes AI | 1 | 1 | 15 | `agnes-3.0-flash,agnes-image-2.5-flash,agnes-video-2.5-flash,executor-primary` |
| 8 | Zhipu GLM-4.7-Flash | 16 | 1 | 30 | `glm-4.7-flash,reviewer-primary`（含幽灵别名残留） |
| 9 | deepseek-ds2api | 1 | **2（停用）** | 30 | `deepseek-chat,deepseek-reasoner,deepseek-chat-search,deepseek-reasoner-search` |
| 15 | openrouter-1 | 1 | 1 | 0 | OpenRouter `:free` 模型组（Key 后 4 位 `75f2`；含 `nvidia/nemotron-3-ultra-550b-a55b:free` 等 16 个） |
| 16 | openrouter-2 | 1 | 1 | 0 | 同上（Key 后 4 位 `9085`） |
| 17 | openrouter-3 | 1 | 1 | 0 | 同上（Key 后 4 位 `ccf6`） |
| 18 | google-gemini | 24 | 1 | 0 | `gemini-3.8-flash,gemini-3.8-flash-lite,gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3-flash-preview` |

快照之后新建、**不在本快照内**的渠道（只按手册文字记录，行级字段未在本轮核实，不编 `base_url` / `type` / `priority`）：

- 渠道 19/20/21（百炼 Bailian）：19 = `qwen3.8-27b`，20 = `qwen3.8-flash`，21 = `qwen3.8-max-0902`。手册只记 `base_url` 为 business-space 域名（不是 `dashscope.aliyuncs.com`，维护期），精确域名未核实。
- 渠道 22/23/24/25（Atria）：22 = `Atria-Dawn`，23 = `intern-s2`，24 = `Agents-A1`，25 = `Sage-A1.5`；手册记 `base_url https://discovery-api.intern-ai.org.cn`（newapi type-1 会补 `/v1`）；4 个渠道 `status` 均为 0 未启用，生产不发。
- 硅基流动渠道**未建**：Key 在 Secure Vault，明文执行方拿不到，是已知卡点；`Qwen/Qwen3.5-9B`、`THUDM/GLM-4-9B-0414` S2 100、金丝雀 3×3/3 已达转正线，但渠道未建，不得报已接进网关。

### 4.3 LiteLLM：未部署

**本枢纽没有 LiteLLM 配置文件，没有 LiteLLM 部署。** 现行路由只用 newapi + `model-health.py`（`route` / `route-layer` / `revive`）+ 金丝雀/守卫脚本，不引新中间件（`production_base.rule` 原文）。

下面这段是**目标格式，未部署**——仅作“如果将来要把四层映射成 LiteLLM 路由表，大致长这样”的草案，不得当现行配置用，不得据此宣称已部署、已接线：

```yaml
# 目标格式，未部署。无 LiteLLM 进程、无此文件落盘、无实测。
# 仅映射 §2 的层 -> 模型，不含任何 endpoint / key / 权重承诺。
router_layers:
  LIGHT: [glm-4-flash-cc, qwen3.8-flash]      # fallback: agnes-3.0-flash
  MEDIUM: [qwen3.8-27b]                       # fallback: glm-4-flash-cc
  COMPLEX: [zen-deepseek]                     # fallback: nemotron-3-ultra, llama-4-scout
  REASONING: cross-supplier-only              # 见 §3，不设固定模型
```

是否引入 LiteLLM 须另行定案（新中间件 = 新控制面改动）；在定案之前，任何 LiteLLM 配置引用均属虚构。

## 5. 429 自动 Fallback 切换

### 5.1 适用范围与前置规则（已落地的，不与本节新代码冲突）

- Zen 超时/429/IP 限流 → **立刻**落网关兜底，不硬重试、不死循环（`zen_binding.fallback_rule` 原文）。
- OpenRouter 429 带 daily-limit → 记 `quota_exhausted`；渠道剩余额度为 0 即停用该渠道，日切（UTC 00:00）后恢复；不硬重试。
- 余额 ≤0 或 402 → 停用渠道、移出池子、记冷却、永不自动恢复（硅基流动守卫口径）。
- CF Neurons ≥9,500 → 3 个 CF 别名置 quarantined、cooldown 到下一个 UTC 0 点。
- 503 与 429 同处理；网关模型超时 10 秒（大管家派单口径）即无缝降级。

### 5.2 429 重试 + Fallback 代码（规范代码）

> 来源标注：本节参数（`Retry-After`、指数退避 `30s×2^n`、最多 3 次再 FALLBACK）按任务指令“按令 19 问 4”给出的口径写。**令 19 问 4 原文未在本轮枢纽 grep 中定位**（查 `state/survey-20261106b.md`、`state/chief-of-staff-overhaul-20261106/memory-four-layers.md` 无命中），本节标注为**待核对令 19 原文**，在核对前不得当已核对的令文引用。指数退避只适用于“同模型可重试的 429”（有 `Retry-After` 且非 quota 耗尽）；§5.1 的 quota/余额类 429 不走重试，直接停用/降级。

```python
#!/usr/bin/env python3
"""规范04 §5：429 自动 Fallback 切换（规范代码，未接线）。

策略：同模型指数退避重试 -> 耗尽后按层 fallback 链切换 -> 仍失败则记录失败。
退避：wait = 30 * (2 ** n) 秒，n = 0,1,2 -> 30s / 60s / 120s；最多 3 次重试。
Retry-After 优先：响应头有 Retry-After（秒数）时取 max(Retry-After, 退避值)。
quota 类 429（daily-limit / 余额耗尽 / 402）不重试，直接 FALLBACK + 停用渠道。
"""
import time
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:3001/v1"  # 现行网关入口，见 §4.1
MAX_RETRIES = 3
BASE_BACKOFF_S = 30

# 层 fallback 链：与 state/gateway-pool.json 各层 fallback 字段一致。
FALLBACK_CHAIN = {
    "LIGHT": ["agnes-3.0-flash"],                 # 再失败 -> 另一主力；仍失败 -> 记录失败，不升 MEDIUM
    "MEDIUM": ["glm-4-flash-cc"],
    "COMPLEX": ["nvidia/nemotron-3-ultra-550b-a55b:free", "llama-4-scout"],
    "REASONING": [],                              # 复核层无 fallback：无异供应商复核方即挂起，见 §3
}

def _is_quota_error(status: int, body: str) -> bool:
    b = (body or "").lower()
    return ("daily-limit" in b or "quota" in b or "insufficient" in b
            or "balance" in b or status == 402)

def _retry_after_seconds(resp) -> int | None:
    raw = resp.headers.get("Retry-After") if resp is not None else None
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None

def _post_once(model: str, messages: list, timeout: int = 60):
    import json
    body = json.dumps({"model": model, "messages": messages}).encode()
    req = urllib.request.Request(
        BASE + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + _gateway_key(),
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read(), resp

def _gateway_key() -> str:
    import os
    key = os.environ.get("NEWAPI_KEY", "")
    if not key:
        raise RuntimeError("NEWAPI_KEY 未设置，拒绝发请求")
    return key

def call_with_fallback(layer: str, model: str, messages: list):
    """返回 (model_used, status, body)。全部失败时返回最后一个模型的失败，并由调用方记失败日志。"""
    candidates = [model] + [m for m in FALLBACK_CHAIN.get(layer, []) if m != model]
    last = (model, 0, b"")
    for cand in candidates:
        for n in range(MAX_RETRIES + 1):
            try:
                status, body, resp = _post_once(cand, messages)
                return cand, status, body
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", "ignore") if hasattr(e, "read") else ""
                last = (cand, e.code, err_body.encode())
                if e.code == 429 and not _is_quota_error(e.code, err_body):
                    if n < MAX_RETRIES:
                        ra = _retry_after_seconds(e)
                        wait = max(BASE_BACKOFF_S * (2 ** n), ra or 0)
                        time.sleep(wait)
                        continue  # 同模型重试：30s / 60s / 120s
                break  # 非 429、quota 类 429、或重试耗尽 -> 换下一个 fallback 模型
            except (urllib.error.URLError, TimeoutError) as e:
                last = (cand, 0, str(e).encode())
                break  # 超时按 10 秒口径降级：不重试同模型，直接 fallback
    return last  # 全部候选耗尽：调用方记录失败，不自动升层、不死循环
```

执行约束（与现有规则一致）：

1. 同模型最多重试 3 次（等待 30s → 60s → 120s），第 4 次 429 不再重试该模型。
2. 重试耗尽后按 `FALLBACK_CHAIN` 换模型；LIGHT 链走完仍失败 → 记录失败，**不自动升级到 MEDIUM**；MEDIUM 链走完仍失败 → 记失败（同层无第二主力）；REASONING 无 fallback。
3. 任何一次成功即停，不继续试下一个候选；失败须记日志（模型、层、HTTP 状态、`Retry-After`、重试次数、最终 fallback 结果），供金丝雀/冷却计数。
4. 本代码是规范草案，未接进总调度/执行器——手册 §6 已记：五类模型失败自动动作未接线，重试链断在 RETRY_WAIT 无人认领（唯一会认领的 cq-csv claimer 定时器已 PAUSED）。接线须另行定案，不得宣称已自动生效。

## 6. 真实运行：ssh 读 `gateway-pool.json`（原文粘贴）

命令（2026-11-06 11:24 CST，枢纽执行）：

```bash
ssh arm-agent 'cd /home/ubuntu/ai-team && date "+%F %T %Z %z" && python3 -c "import json; d=json.load(open(\"state/gateway-pool.json\")); print(json.dumps({k:d[k] for k in [\"date\",\"status\",\"tier_a\",\"tier_b\",\"pool_count\",\"primary\",\"suppliers\",\"pool_minimum_2_met\",\"pool_minimum_3_met\"]},ensure_ascii=False,indent=2)); print(json.dumps(d[\"production_base\"],ensure_ascii=False,indent=2))"'
```

真实输出（原文，未改）：

```text
2026-11-06 11:24:23 CST +0800
{
  "date": "2026-11-05",
  "status": "final-round2",
  "tier_a": [
    "glm-4-flash-cc",
    "qwen3.8-flash"
  ],
  "tier_b": [],
  "pool_count": 2,
  "primary": "glm-4-flash-cc",
  "suppliers": [
    "Zhipu",
    "Alibaba-Bailian"
  ],
  "pool_minimum_2_met": true,
  "pool_minimum_3_met": false
}
{
  "strategy": "2+1 死守生产底座",
  "gateway_2": [
    "glm-4-flash-cc",
    "qwen3.8-flash"
  ],
  "plus_1": "Zen (DeepSeek, opencode/deepseek-v4.1-flash-free) - COMPLEX 代码层主力/备用腿, 不计入 pool_count, 不进 tier_a/tier_b",
  "rule": "生产任务只走 2+1; 测试池模型须经网关规则 (canary + S2 + 连续3轮) 才能转正, 不人工直派; 不引新中间件, 只用现有 newapi + model-health 池子",
  "status": "2026-11-05 gemini-3.5-flash-lite OUT_OF_POOL (Order 17); production base now glm-4-flash-cc + qwen3.8-flash + Zen; Zen leg still unavailable (timeout), fallback nemotron-3-ultra verified."
}
```

另：`state/l4-20261105-eval/` 实查存在 `baseline-20261105.json`、`golden-set-100.json`、`golden-set-100.md`、`l4_baseline.py` 及 `backup-20261105-20261105-200840/`；`scripts/review-supplier-check.py` 实查存在，内容与 §3.1 引用一致；`scripts/model-health.py` 的 `cmd_route_layer`（行 229 起）实查存在，`REASONING` 的 `pool_list` 硬编码含 `gemini-3.5-flash-lite`（见 §3.3 缺口 2）。

## 7. 未办成的部分及原因

1. **令 19 问 4 原文未核对**：§5.2 参数按任务指令口径写，枢纽 grep 未定位原文，须核对令 19 原文后再去掉“待核对”标注。
2. **§3.3 的三个缺口未修**：检查脚本缺 Alibaba 映射；`route-layer` 的 REASONING 列表含已出池模型；渠道 3/4/5/8 的 `reviewer-primary` 别名残留未清理（须改 MySQL + 验证重载，控制面改动，另行定案）。
3. **渠道 19–25 行级字段未核实**：快照（2026-11-05 06:22）只到渠道 18；百炼/Atria 渠道行须以实时库查询核实后补进 §4.2，不得凭手册文字补全 `type` / `priority` / `base_url`。
4. **§5 代码未接线**：规范草案，未接入总调度/执行器；RETRY_WAIT 认领断链问题仍在，见 §5.2 第 4 条。
5. **§2 的三处文件内冲突未修**：`MEDIUM.status` / `MEDIUM.note` / `butler_split` 文本过期，须由池文件维护方更新，本规范只记录、不擅自改枢纽文件。
6. **复核能力缺口**：智谱执行的任务当前无健康 Google 复核方（§3.2），须等 `gemini-3-flash-preview` 冷却结束 + revive 重测通过，或另行定补位复核方。

## 8. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-11-06 | 初版待验证：按 2026-11-06 11:24 CST 枢纽实测写成。真源 `gateway-pool.json`、`HANDBOOK.md`、`l4-20261105-eval/`、`EVIDENCE-20261105.md`、`model-health.py`、`review-supplier-check.py`、`gateway-channels.json` 快照。 |
