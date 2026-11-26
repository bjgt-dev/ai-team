#!/bin/bash
# kb-ask.sh v3 — 查知识库（只用 grep，实时）
# 用法：kb-ask.sh "问题"
# 机制（2026-11-10 收敛后）：单一共享记忆 = 枢纽 knowledge/（Markdown 文件，
#   按领域分（正典见 knowledge/驾驶舱/知识库分类正典-20261123.md）：业务/商业/工具/研究/角色/驾驶舱/主理人/core/intel/ip-content/收件箱；
#   品牌/ 无目录、未落地，不作领域；根目录仅短剧历史遗留与 canary 探针记录；
#   检索只用直接 grep（实时，排除 _archive/）；向量索引在跑，但 kb-ask 不用它：
#   kb-rebuild-watch cron 每 30 分钟（crontab 第 147 行，日志 ~/ai-team/logs/kb-rebuild-watch.log，
#   注意不是 state/logs/ 下那个 2026-11-10 旧文件）；index.npz/index.json 为 live 文件
#   （state/kb-retrieval-20261106/，2026-11-26 14:26 由 11:30 轮重建写出，51MB，13,576 chunks；
#   单次重建约 3 小时，30 分钟间隔内多轮 SKIP 是构建占锁、不是 cron 停；_archive-v1/ 只是
#   2026-11-10 旧副本）。三处旧口径（cron 已停/已归档/索引已停）均已证伪，核实 2026-11-26 A 批。
# 查不到就明说没查到，不许编。
set -u
if [ $# -lt 1 ]; then
  echo "用法：kb-ask.sh \"问题\"" >&2
  exit 2
fi
Q="$*"
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=30"
ssh $SSH_OPTS arm-agent "cd ~/ai-team/knowledge && python3 -c \"
import re, sys, subprocess
q = '''$Q'''
terms = [t for t in re.split(r'[\\\\s,，。；;：:、/\\\\-]+', q) if len(t) >= 2]
terms = list(dict.fromkeys(terms))[:6]
if not terms:
    print('无有效检索词')
    sys.exit(0)
seen = set()
for t in terms:
    try:
        out = subprocess.run(['grep', '-r', '-i', '-n', '-m', '1', '-F', '--exclude-dir=_archive', t, '.'], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        continue
    for line in out.strip().split('\\\\n'):
        if not line or ':' not in line:
            continue
        f = line.split(':', 1)[0]
        if f in seen:
            continue
        seen.add(f)
        print('[实时] %s :: %s' % (f, line[:220]))
if not seen:
    print('grep 无结果')
\" 2>&1"
RC=$?
# 证据（2026-11-13 root-gate）：查库行为记入 check-ledger，只记时间+问题哈希，不记问题内容；只追加，写失败不影响主流程与退出码。台账位置按脚本自身位置定（枢纽脚本→枢纽 state，本地脚本→本地 tools），不靠目录存在性判断。
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
case "$_SCRIPT_DIR" in *ai-team*) _LED="/home/ubuntu/ai-team/state/check-ledger.jsonl";; *) _LED="$HOME/workspace/tools/check-ledger.jsonl";; esac
{ printf '{"ts":"%s","tool":"kb-ask","qhash":"%s"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(printf '%s' "$Q" | md5sum | cut -d' ' -f1)" >> "$_LED"; } 2>/dev/null || true
exit $RC
