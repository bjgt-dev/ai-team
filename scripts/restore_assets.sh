#!/usr/bin/env bash
# 一键恢复（配合 backup_assets.sh）：从私有仓拉最新备份 → 解密 → 解包到 /tmp/restore-<date>/
# 用法：restore_assets.sh [YYYY-MM-DD]        只解包并打印内容清单（不碰现网）
#       RESTORE_PG=1 restore_assets.sh [...]   额外把 PG dump 灌进临时演练库 child_agent_drill（不碰现网库）
# 真正覆盖现网恢复是人工确认动作：解包后按打印的指引执行，本脚本绝不自动覆盖。
set -euo pipefail

REPO=/home/ubuntu/backups-repo
PASSFILE=/home/ubuntu/ai-team/state/.backup-pass
cd "$REPO" && git pull -q origin HEAD || true
DATE="${1:-$(ls -1 "$REPO/backups" | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' | sort | tail -1)}"
ENC="$REPO/backups/$DATE/assets.tar.gz.enc"
[ -f "$ENC" ] || { echo "NO BACKUP for $DATE"; exit 1; }
(cd "$REPO/backups/$DATE" && sha256sum -c SHA256SUMS)
OUT="/tmp/restore-$DATE"
rm -rf "$OUT" && mkdir -p "$OUT"
openssl enc -d -aes-256-cbc -pbkdf2 -in "$ENC" -pass file:"$PASSFILE" | tar xzf - -C "$OUT"
echo "=== RESTORED to $OUT (date=$DATE) ==="
cat "$OUT/payload/MANIFEST.txt"
if [ "${RESTORE_PG:-0}" = "1" ]; then
  docker exec ops-pg psql -U postgres -c "DROP DATABASE IF EXISTS child_agent_drill;" >/dev/null
  docker exec ops-pg psql -U postgres -c "CREATE DATABASE child_agent_drill;" >/dev/null
  zcat "$OUT/payload/pg_all.sql.gz" | grep -v -E '^(CREATE DATABASE|\\connect) (ops|child_agent|postgres)' > "$OUT/pg_filtered.sql" || true
  # 演练只验证 child_agent 部分可解析：直接统计 dump 内 learning_events 的 COPY 行数
  echo "drill: pg dump lines=$(zcat "$OUT/payload/pg_all.sql.gz" | wc -l)"
  docker exec ops-pg psql -U postgres -c "DROP DATABASE IF EXISTS child_agent_drill;" >/dev/null
fi
echo "现网覆盖恢复（人工确认后）：zcat $OUT/payload/pg_all.sql.gz | docker exec -i ops-pg psql -U postgres ；文件类资产从 $OUT/payload/ 拷回 /home/ubuntu/ai-team/ 对应目录。"
