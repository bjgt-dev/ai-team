---
name: vps-gatekeeper
description: 在 VPS 上部署 AI 团队验收端：verify.py 门禁、ingest.sh 入库、ingest 受限账户、scan_inbox.sh 多路径扫描。适用于任何需要"执行与验收分离"的新节点。
---

# VPS Gatekeeper：验收端部署

## 架构定位

VPS 是**控制面**：只做调度（生成契约）与验收（门禁判定）。绝不执行任务，绝不直接写 knowledge/。

```
contracts/pending/{cue,muse}/  → 契约（只出）
inbox/<task_id>/               → 证据包（只进）
knowledge/<task_id>/           → PASS 入库
quarantine/<task_id>/          → REJECT/NEED_HUMAN 隔离
```

## 1. verify.py：唯一裁判

### 核心逻辑

```python
# 四种可代码判定的验收类型，缺一不可
- file_exists:    文件是否存在
- file_min_size:  字节数下限
- file_contains:  内容包含关键词
- exit_code:      进程退出码

# 三重核对
1. manifest.json sha256 校验（文件未被篡改）
2. receipts.jsonl 交叉核对（声称写了 N 个文件，回执必须有 N 条）
3. acceptance_criteria 逐条判定（契约里写死的条件）
```

### 路径逃逸加固

```python
# target 含 ../ 或绝对路径 → 直接 FAIL
if ".." in target or target.startswith("/"):
    return FAIL("path escapes bundle")
```

**血泪教训**：2026-10-20 实测拦截 3 次 `../../../etc/passwd` 逃逸尝试。

### 为什么门禁必须独立

当天发现某执行脚本 `gen_evidence.py` 批量为所有文件生成 `write_file/success` 回执——如果执行器和裁判同一权限，这种伪造查不出来。**回执必须在执行动作发生时实时写入，事后补的一律视为伪造。**

## 2. ingest.sh：入库脚本

```bash
#!/usr/bin/env bash
set -euo pipefail
TEAM_ROOT="${TEAM_ROOT:-/home/ubuntu/ai-team}"
TASK_ID="$1"
BUNDLE="$TEAM_ROOT/inbox/$TASK_ID"
CONTRACT="$TEAM_ROOT/contracts/$TASK_ID.json"

OUT=$(python3 "$TEAM_ROOT/scripts/verify.py" "$BUNDLE" "$CONTRACT" || true)
VERDICT=$(echo "$OUT" | python3 -c 'import sys,json; print(json.load(sys.stdin)["verdict"])')

case "$VERDICT" in
  PASS)
    # L3 评审（可选）：调 verifier-stable 查幻觉
    REVIEW_OUT=$(python3 "$TEAM_ROOT/scripts/review_gate.py" "$BUNDLE" "$TASK_ID" 2>/dev/null || echo '{"verdict":"FAIL"}')
    if [ "$(echo "$REVIEW_OUT" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("verdict","FAIL"))')" = "PASS" ]; then
      mkdir -p "$TEAM_ROOT/knowledge/$TASK_ID"
      cp -r "$BUNDLE/artifacts/." "$TEAM_ROOT/knowledge/$TASK_ID/"
      echo "INGESTED $TASK_ID"
    else
      mkdir -p "$TEAM_ROOT/quarantine/$TASK_ID"
      mv "$BUNDLE" "$TEAM_ROOT/quarantine/$TASK_ID/bundle"
      echo "QUARANTINED $TASK_ID verdict=REVIEW_FAIL"
    fi ;;
  REJECT|NEED_HUMAN)
    mkdir -p "$TEAM_ROOT/quarantine/$TASK_ID"
    mv "$BUNDLE" "$TEAM_ROOT/quarantine/$TASK_ID/bundle"
    echo "QUARANTINED $TASK_ID verdict=$VERDICT" ;;
esac
```

## 3. ingest 受限账户

```bash
# 创建用户（无密码登录，只认 key）
sudo useradd -m -s /bin/bash ingest
sudo passwd -l ingest  # 锁定密码

# SSH ACL（~/.ssh/authorized_keys 用 command= 限制）
# 更简单的做法：用 setfacl 做文件级控制
setfacl -m u:ingest:rx /home/ubuntu/ai-team/contracts/pending/
setfacl -m u:ingest:rwx /home/ubuntu/ai-team/inbox/
setfacl -m u:ingest:--- /home/ubuntu/ai-team/knowledge/
setfacl -m u:ingest:--- /home/ubuntu/ai-team/scripts/
```

**权限矩阵**：

| 路径 | ingest 权限 |
|------|-------------|
| contracts/pending/ | 只读（拉契约） |
| inbox/ | 只写（传证据） |
| knowledge/ | 无（防直接入库） |
| scripts/ | 无（防篡改门禁） |

## 4. scan_inbox.sh：多路径扫描

```bash
#!/usr/bin/env bash
# 每分钟 cron 运行
# 扫描顺序：pending/cue/ → pending/muse/ → pending/
# 同 task_id 多处出现且内容不一致 → CONTRACT_PATH_CONFLICT 跳过
```

**避坑**：多 lane 架构下，契约的 canonical 路径必须唯一。扫描到冲突时宁可跳过，不可随便取一个。

## 5. Cron 配置

```bash
# 系统 cron（非 OpenClaw）
* * * * * /home/ubuntu/ai-team/scripts/scan_inbox.sh >> /var/log/scan_inbox.log 2>&1
```
