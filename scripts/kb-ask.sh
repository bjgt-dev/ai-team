#!/bin/bash
# kb-ask.sh v3 — 查知识库（只用 grep，实时）
# 用法：kb-ask.sh "问题"
# 机制（2026-11-10 收敛后）：单一共享记忆 = 枢纽 knowledge/（Markdown 文件，
#   按领域分：研究/驾驶舱/业务/工具/商业/角色/收件箱/品牌/主理人）；
#   检索只用直接 grep（实时，排除 _archive/）；向量索引已停（kb-rebuild-watch
#   cron 已停，index.npz/index.json 已归档到 kb-retrieval-20261106/_archive-v1/）。
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
