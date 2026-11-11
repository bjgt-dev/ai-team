#!/usr/bin/env bash
# 核心资产异地备份（2026-10-20 董事长令）：knowledge + state + PG 全量 dump + omemo 数据
# 打包 → AES-256 加密 → 推送私有 GitHub 仓 bjgt-dev/noesis-assets-backup
# 加密口令在枢纽 state/.backup-pass（600），另一副本在 Twt2026 本机 hidden_files（双故障域）。
# 恢复：restore_assets.sh [YYYY-MM-DD]（默认最新），见该脚本。
set -euo pipefail

DATE=$(date +%F)
BASE=/home/ubuntu/ai-team
REPO=/home/ubuntu/backups-repo
PASSFILE=$BASE/state/.backup-pass
WORK=$(mktemp -d /tmp/backup-XXXXXX)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/payload" "$REPO/backups/$DATE"

# 1) 文件资产
cp -r "$BASE/knowledge" "$WORK/payload/knowledge"
mkdir -p "$WORK/payload/state"
sudo -n cp -a "$BASE/state/." "$WORK/payload/state/"
sudo -n chown -Rh "$(id -u):$(id -g)" "$WORK/payload/state"
chmod -R u+rwX "$WORK/payload/state"
rm -f "$WORK/payload/state/.backup-pass"
cp -r "$BASE/contracts" "$WORK/payload/contracts" 2>/dev/null || true
cp -r "$BASE/muse-worker" "$WORK/payload/muse-worker" 2>/dev/null || true
tar czf "$WORK/payload/omemo.tar.gz" --exclude=venv --exclude=__pycache__ --exclude='*.log' -C /home/ubuntu omemo

# 1b) browser profiles (expert-channel login state; chairman order 2026-10-20:
# long-term assets, restore from backup instead of asking for re-login)
sudo -n bash -c '
  LIST="$1"; : > "$LIST"
  for p in claude chatgpt deepseek deepseek2 gemini instabox instabox2; do
    d="/opt/model-profiles/$p"; [ -d "$d" ] || continue
    echo "model-profiles/$p/Local State" >> "$LIST"
    for f in Network "Local Storage" "Session Storage" "Login Data" "Login Data-journal" Preferences "Secure Preferences" "Web Data"; do
      [ -e "$d/Default/$f" ] && echo "model-profiles/$p/Default/$f" >> "$LIST"
    done
  done' _ "$WORK/profiles-list.txt"
sudo -n tar czf "$WORK/payload/profiles.tar.gz" -C /opt -T "$WORK/profiles-list.txt" || echo "profiles tar FAILED" >&2

# 2) PG 全量 dump（ops-pg 内所有库）
docker exec ops-pg pg_dumpall -U postgres | gzip > "$WORK/payload/pg_all.sql.gz"
EVENTS=$(docker exec ops-pg psql -U postgres -d child_agent -At -c "SELECT count(*) FROM learning_events;" 2>/dev/null || echo "?")

# 2b) NewAPI MySQL dump（2026-10-24 董事长总纲模块二：备份包必须含 NewAPI DB）
MYSQL_DUMP_OK=1
docker exec ai-stack-new-api-db-1 sh -c 'mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction oneapi' 2>/dev/null | gzip > "$WORK/payload/mysql-oneapi.sql.gz" || MYSQL_DUMP_OK=0
if [ "$MYSQL_DUMP_OK" = "0" ] || [ ! -s "$WORK/payload/mysql-oneapi.sql.gz" ] || [ "$(stat -c %s "$WORK/payload/mysql-oneapi.sql.gz" 2>/dev/null || echo 0)" -lt 1000 ]; then echo "BACKUP_FAILED mysql dump failed" >&2; exit 1; fi

# 3) 清单
{
  echo "date=$DATE"
  echo "learning_events=$EVENTS"
  (cd "$WORK/payload" && sha256sum $(find . -type f | sort))
} > "$WORK/payload/MANIFEST.txt"

# 4) 打包 + 加密
tar czf "$WORK/assets.tar.gz" -C "$WORK" payload
openssl enc -aes-256-cbc -salt -pbkdf2 -in "$WORK/assets.tar.gz" -out "$REPO/backups/$DATE/assets.tar.gz.enc" -pass file:"$PASSFILE"
sha256sum "$REPO/backups/$DATE/assets.tar.gz.enc" > "$REPO/backups/$DATE/SHA256SUMS"

# 5) 只保留最近 14 天
ls -1d "$REPO/backups/"????-??-?? 2>/dev/null | sort | head -n -14 | while read -r d; do TRASH="/data/trash/$(date +%F)"; mkdir -p "$TRASH" 2>/dev/null || true; b=$(basename -- "$d"); dest="$TRASH/$b"; [ -e "$dest" ] && dest="$TRASH/${b}_$(date +%H%M%S)"; mv -- "$d" "$dest" 2>/dev/null || true; done

# 6) 提交推送
cd "$REPO"
git add -A
git -c user.name="Twt2026 Backup" -c user.email="backup@noesis" commit -q -m "backup $DATE (events=$EVENTS)" || { echo "nothing to commit"; exit 0; }
git push -q origin HEAD
echo "BACKUP_OK $DATE events=$EVENTS size=$(du -h "$REPO/backups/$DATE/assets.tar.gz.enc" | cut -f1)"
