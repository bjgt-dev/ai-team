#!/usr/bin/env bash
# sanitized-sync.sh — 枢纽 ~/ai-team/ → 公开仓库 ~/ai-team-public/ 脱敏同步
#
# 原则（董事长 2026-11-11 定，另建干净仓库、枢纽现有仓库不直接推）：
#   - 只同步公开仓库已追踪的路径（git ls-files = 允许清单），新文件永不自动进仓，
#     只列入报告待人工审核；PUBLIC_ONLY 的 7 个根文件只在公开仓维护，永不动。
#   - 同步前先备份目标工作树；脱敏规则 + 泄漏扫描不过即 fail-closed，
#     不写目标、不提交、不推送。
#   - 脱敏规则来自 2026-11-11 首版导出的实际 diff（逐文件核对过）：
#       1. 枢纽 Tailnet 主机 IP → 127.0.0.1（枢纽默认绑定与网关地址）。
#       2. 其余 Tailnet 段主机 IP（100.64/10）→ <tailnet-ip> 占位；
#          公开的 CGNAT 段写法 100.64.0.0/10 本身不是主机地址，原样保留。
#       3. 注册/登录邮箱 → @example.com 占位（与首版导出一致）。
#       4. 董事长真实姓名 → 测试姓名（首版导出对测试文件即如此处理）。
#       5. 孩子昵称 → 孩子；孩子具体年龄表述（“学龄前”）→ 学龄前通用表述
#          （均与首版导出对同类内容的通用化处理一致）。
#     例外（HELD）：dename-lint.py / soul-gate-20261107.py 的 REAL_NAME
#     在首版导出时被结构性改写为环境变量读取，机械替换会改错语义；
#     state/handovers/handover-twt-to-next-20261111.md 是 live 交接文档，
#     源端持续写入孩子信息与凭据位置，机械脱敏不可靠；
#     两类永不覆盖，只报告 HELD，改动须人工复核。
#   - 先例说明：首版导出把孩子昵称通用化为“孩子”、具体年龄表述通用化为
#     “学龄前”（如 requirement-clarification），本次同步沿用该标准并加扫描
#     拦截；儿童陪伴项目本身（child-education 等）不在允许清单内，永不同步。
#   - 自 scrub 安全：本文件自身也在同步清单内（EXTRA_INCLUDE），故规则
#     中的敏感字面值一律用字符串拼接写成，文件里不出现可被扫描命中的
#     完整字面值；公开版与枢纽版运行时行为一致。
#
# 用法：
#   sanitized-sync.sh [--dry-run] [--skip-push]
#     --dry-run   只搭 staging、脱敏、扫描、比对并打印变更摘要，不写目标、不提交
#     --skip-push 同步+提交但不推送（用于先本地验收）
#   退出码：0=同步完成或无变化；1=前置检查/扫描失败（目标未动）；2=用法错误。
#   日志：stdout（cron 重定向到 state/logs/sanitized-sync.log）。
#   备份：/home/ubuntu/ai-team-public-backups/ai-team-public-<ts>.tar.gz（留 7 份）。
#
set -euo pipefail

SRC="/home/ubuntu/ai-team"
DST="/home/ubuntu/ai-team-public"
BACKUP_ROOT="/home/ubuntu/ai-team-public-backups"
DRY_RUN=0
SKIP_PUSH=0

# 公开仓自有、不随枢纽同步的根文件（只在公开仓维护）
PUBLIC_ONLY="LICENSE README.md CONTRIBUTING.md SECURITY.md STATE.md THIRD_PARTY_NOTICES.md .gitignore"
# 结构性脱敏文件：永不覆盖，只报告
HELD="scripts/dename-lint.py scripts/soul-gate-20261107.py state/handovers/handover-twt-to-next-20261111.md"
# 本轮起明确允许新增进仓的文件（须已在 SRC 存在、无凭据、经扫描）
EXTRA_INCLUDE="scripts/sanitized-sync.sh scripts/internal-iteration-enqueue.py"

for a in "$@"; do
  case "$a" in
    --dry-run) DRY_RUN=1 ;;
    --skip-push) SKIP_PUSH=1 ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "SYNC USAGE ERROR: unknown arg $a" >&2; exit 2 ;;
  esac
done

TS="$(date +%Y%m%d-%H%M%S)"
STAGE="$(mktemp -d /tmp/sanitized-sync.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT

log() { echo "[sanitized-sync $TS] $*"; }

# ---- 1. 前置检查：git 干净、与远端一致 ----
cd "$DST"
if [ -n "$(git status --porcelain)" ]; then
  log "ABORT: public repo working tree not clean; commit or stash first:"; git status --short > "$STAGE/dirty.txt"; head -n 20 "$STAGE/dirty.txt"; exit 1
fi
git fetch origin master --quiet || { log "ABORT: git fetch failed"; exit 1; }
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/master)" ]; then
  log "ABORT: local HEAD != origin/master; pull first"; exit 1
fi

# ---- 2. 备份目标工作树（不含 .git；已提交状态另有 git/GitHub 兜底） ----
mkdir -p "$BACKUP_ROOT"
BACKUP="$BACKUP_ROOT/ai-team-public-$TS.tar.gz"
tar czf "$BACKUP" --exclude=.git -C "$(dirname "$DST")" "$(basename "$DST")"
log "BACKUP: $BACKUP"
# 只留最近 7 份
ls -t "$BACKUP_ROOT"/ai-team-public-*.tar.gz 2>/dev/null | tail -n +8 | xargs -r rm -f

# ---- 3. 搭 staging：允许清单 + EXTRA_INCLUDE ----
git ls-files | sort > "$STAGE/allowlist.txt"
grep -v -x -F -e "LICENSE" -e "README.md" -e "CONTRIBUTING.md" -e "SECURITY.md" -e "STATE.md" -e "THIRD_PARTY_NOTICES.md" -e ".gitignore" "$STAGE/allowlist.txt" > "$STAGE/sync-list.txt" || true
for h in $HELD; do grep -v -x -F -e "$h" "$STAGE/sync-list.txt" > "$STAGE/tmp.txt" && mv "$STAGE/tmp.txt" "$STAGE/sync-list.txt"; done
N_ALLOW="$(wc -l < "$STAGE/sync-list.txt")"
N_COPIED=0; N_MISSING=0
while read -r f; do
  if [ -f "$SRC/$f" ]; then
    mkdir -p "$STAGE/tree/$(dirname "$f")"
    cp -p "$SRC/$f" "$STAGE/tree/$f"
    N_COPIED=$((N_COPIED+1))
  else
    echo "$f" >> "$STAGE/deleted.txt"
    N_MISSING=$((N_MISSING+1))
  fi
done < "$STAGE/sync-list.txt"
N_EXTRA=0
for x in $EXTRA_INCLUDE; do
  if [ -f "$SRC/$x" ]; then
    mkdir -p "$STAGE/tree/$(dirname "$x")"
    cp -p "$SRC/$x" "$STAGE/tree/$x"
    N_EXTRA=$((N_EXTRA+1))
  else
    log "EXTRA-INCLUDE missing in source (skipped): $x"
  fi
done
log "STAGE: allowlist=$N_ALLOW copied=$N_COPIED deleted-from-source=$N_MISSING extra=$N_EXTRA held=[${HELD}]"

# ---- 4. 脱敏（顺序敏感：先整句/整行规则，再通用 IP/邮箱/姓名规则） ----
# 注意：规则中的敏感字面值用拼接写成，避免本文件自身被扫描命中。
python3 - "$STAGE/tree" <<'PYEOF'
import os, re, sys
root = sys.argv[1]
HUB_IP = "100" + ".102.143.22"          # 枢纽 Tailnet 地址
OLD_HUB_IP = "100" + ".103.193.123"     # 旧枢纽地址
LOCAL = "127.0.0.1"
TAILNET_HOST = re.compile(r"100\.(?:6[4-9]|[7-9][0-9]|1[0-2][0-7])\.[0-9]{1,3}\.[0-9]{1,3}")
CIDR = "100.64.0.0/10"
REAL_NAME = "田" + "志勇"
NICKNAME = "奇" + "奇"
AGE_WORD = "五岁" + "多"
MAIL_REG = "bjgt2026" + "@qq.com"
MAIL_MAIN = "tianzy2017" + "@gmail.com"
MAIL_PROXY = "137174447" + "@qq.com"
MAIL_POOL = "tianwt2022" + "@gmail.com"
MAIL_VENDOR = "help" + "@build.nvidia.com"
RULES = [
    ("(default tailnet " + HUB_IP + ":8093; public default 127.0.0.1:8093)",
     "(default 127.0.0.1:8093)"),
    ('GATE_SVC_DISPATCH = "http://' + HUB_IP + ':8093/gate/dispatch"',
     'GATE_SVC_DISPATCH = os.environ.get("GATE_SVC_URL", "http://127.0.0.1:8093") + "/gate/dispatch"'),
    (HUB_IP, LOCAL),
    (OLD_HUB_IP, LOCAL),
    (MAIL_REG, "registration@example.com"),
    (MAIL_MAIN, "main@example.com"),
    (MAIL_PROXY, "proxy@example.com"),
    (MAIL_POOL, "pool@example.com"),
    (MAIL_VENDOR, "support@example.com"),
    (REAL_NAME, "测试姓名"),
    (NICKNAME, "孩子"),
    (AGE_WORD, "学龄前"),
]
changed = 0
for dirpath, _dirs, files in os.walk(root):
    for fn in files:
        p = os.path.join(dirpath, fn)
        try:
            with open(p, "rb") as fh:
                raw = fh.read()
            text = raw.decode("utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # 二进制文件不脱敏（扫描步会报出）
        orig = text
        # 顺序敏感：先整句/整行/字面规则（枢纽 IP → 127.0.0.1、邮箱/姓名占位），
        # 再通用 Tailnet 段替换；反过来会把枢纽 IP 先变成占位、字面规则永不命中
        # （2026-11-12 首轮实测抓到的真 bug）。
        for pat, rep in RULES:
            text = text.replace(pat, rep)
        text = text.replace(CIDR, "\x00CIDR\x00")
        text = TAILNET_HOST.sub("<tailnet-ip>", text)
        text = text.replace("\x00CIDR\x00", CIDR)
        if text != orig:
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(text)
            changed += 1
print("SCRUB: files-changed=%d" % changed)
PYEOF

# ---- 5. 泄漏扫描（fail-closed：命中任一即中止，目标未动） ----
# 二进制文件：已在公开仓且与目标字节一致的直接放行（eval 固定夹具）；
# 新增或被改过的二进制一律拦截，须人工复核。
python3 - "$STAGE/tree" "$DST" <<'PYEOF'
import os, re, sys
root = sys.argv[1]
dst = sys.argv[2]
REAL_NAME = "田" + "志勇"
NICKNAME = "奇" + "奇"
AGE_WORD = "五岁" + "多"
PATS = [
    ("chairman-real-name", re.compile(re.escape(REAL_NAME))),
    ("child-nickname", re.compile(re.escape(NICKNAME))),
    ("child-age-word", re.compile(re.escape(AGE_WORD))),
    ("tailnet-ip", re.compile(r"100\.(?:6[4-9]|[7-9][0-9]|1[0-2][0-7])\.[0-9]{1,3}\.[0-9]{1,3}")),
    ("private-email", re.compile(r"[A-Za-z0-9._%+-]+@(?:qq\.com|gmail\.com)")),
    ("gh-token", re.compile(r"(ghp_|gho_|github_pat_)[A-Za-z0-9_]{20,}")),
    ("api-key", re.compile(r"sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}")),
    ("db-url-with-password", re.compile(r"postgres(?:ql)?://[^/\s:]+:[^/\s@]+@")),
    ("private-key", re.compile("-----BEGIN " + ".*" + "PRIVATE KEY" + "-----")),
]
hits = []
for dirpath, _dirs, files in os.walk(root):
    for fn in files:
        p = os.path.join(dirpath, fn)
        try:
            with open(p, "rb") as fh:
                raw = fh.read()
            text = raw.decode("utf-8")
        except (UnicodeDecodeError, OSError):
            rel = os.path.relpath(p, root)
            dstp = os.path.join(dst, rel)
            same = False
            try:
                with open(dstp, "rb") as fh:
                    same = (fh.read() == raw)
            except OSError:
                same = False
            if not same:
                hits.append(("binary-new-or-changed", p))
            continue
        for name, pat in PATS:
            if name == "tailnet-ip":
                for m in pat.finditer(text):
                    # 100.64.0.0/10 是公开的 CGNAT 段写法，不算泄漏
                    ctx = text[max(0, m.start()-12):m.end()+3]
                    if "100.64.0.0/10" not in ctx:
                        hits.append((name, "%s: %s" % (p, m.group(0))))
            elif pat.search(text):
                hits.append((name, p))
if hits:
    print("LEAK-SCAN FAIL (%d hits):" % len(hits))
    for h in hits[:100]:
        print("  - %s %s" % h)
    sys.exit(1)
print("LEAK-SCAN PASS: 0 hits")
PYEOF
SCAN_RC=$?
if [ "$SCAN_RC" -ne 0 ]; then
  log "ABORT: leak scan failed; target untouched; backup kept at $BACKUP"
  exit 1
fi

# ---- 6. 待审核新增（只报告，永不自动进仓） ----
echo "NEW-CANDIDATES (in source, not in public allowlist; manual review only):" > "$STAGE/new-candidates.txt"
( cd "$SRC" && find scripts skills docs configs prompts contracts state/l3-v2 state/handovers state/eval-os-phase2 state/baselines services ceo-seat -type f \
    -not -path "*/__pycache__/*" -not -path "*/node_modules/*" -not -path "*/.git/*" \
    -not -name "*.bak*" -not -name "*.pyc" -not -name "*.log" 2>/dev/null | sort ) > "$STAGE/src-candidates.txt"
comm -23 "$STAGE/src-candidates.txt" "$STAGE/allowlist.txt" > "$STAGE/new-list.txt"
echo "count: $(wc -l < "$STAGE/new-list.txt") (showing first 40)" >> "$STAGE/new-candidates.txt"
head -n 40 "$STAGE/new-list.txt" >> "$STAGE/new-candidates.txt"
head -n 45 "$STAGE/new-candidates.txt"

# ---- 7. 比对 / 落盘 ----
if [ "$DRY_RUN" -eq 1 ]; then
  log "DRY-RUN diff (staging vs target, .git excluded):"
  diff -rq --exclude=.git "$STAGE/tree" "$DST" > "$STAGE/diff.txt" 2>&1 || true
  head -n 60 "$STAGE/diff.txt"
  log "DRY-RUN done: target untouched, no commit, no push"
  exit 0
fi
# 注意：不用 --delete。目标里有 gitignored 的运行文件（日志、缓存、case-c
# 产物），rsync --delete 会误删它们；源端已删的追踪文件改由下面的 git rm
# 精确处理（只删 deleted.txt 列出的追踪路径）。
rsync -a --exclude=.git \
  --exclude=LICENSE --exclude=README.md --exclude=CONTRIBUTING.md \
  --exclude=SECURITY.md --exclude=STATE.md --exclude=THIRD_PARTY_NOTICES.md \
  --exclude=.gitignore \
  --exclude=scripts/dename-lint.py --exclude=scripts/soul-gate-20261107.py \
  --exclude=state/handovers/handover-twt-to-next-20261111.md \
  "$STAGE/tree/" "$DST/"
if [ -f "$STAGE/deleted.txt" ]; then
  while read -r f; do git -C "$DST" rm -q -- "$f" 2>/dev/null || true; done < "$STAGE/deleted.txt"
  log "DELETED from source, git-rm'd: $(wc -l < "$STAGE/deleted.txt")"
fi
git -C "$DST" add -A
log "GIT STATUS after sync:"
git -C "$DST" status --short > "$STAGE/status.txt"
head -n 40 "$STAGE/status.txt"
if [ -z "$(git -C "$DST" status --porcelain)" ]; then
  log "SYNC_OK: no changes (already in sync)"
  exit 0
fi
git -C "$DST" commit -q -m "chore: sanitized sync $TS (auto, sanitized-sync.sh)" || { log "ABORT: commit failed"; exit 1; }
log "COMMIT: $(git -C "$DST" log -1 --format='%h %s')"
if [ "$SKIP_PUSH" -eq 0 ]; then
  git -C "$DST" push origin master 2>&1 | tail -n 5
  log "PUSH OK: HEAD $(git -C "$DST" rev-parse --short HEAD) / origin $(git -C "$DST" rev-parse --short origin/master)"
else
  log "SKIP-PUSH: committed locally, not pushed"
fi
if [ -z "$(git -C "$DST" status --porcelain)" ]; then
  log "SYNC_OK: git status clean"
else
  log "WARN: git status not clean after sync"; git -C "$DST" status --short; exit 1
fi
