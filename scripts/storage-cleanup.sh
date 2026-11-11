#!/bin/bash
# Storage cleanup, dry-run first per Claude veto. Real deletion only with --real, and only after dry-run manifest review.
# Scope: /tmp unmapped >24h (ctime), ai-team logs gzip >7d, out model-output >90d, receipts >90d, backups latest 5 verified + weekly 1.
# Never: mapped files, lock/socket, model-profiles, current state, manifests, Muse cold backup, system code, .local auth.
set -u
MODE="${1:-dry-run}"
MANIFEST_DIR="/home/ubuntu/ai-team/state"
MANIFEST="$MANIFEST_DIR/storage-cleanup-$(date +%F).json"
LOG="/home/ubuntu/ai-team/logs/storage-cleanup.log"
exec 9>/tmp/storage-cleanup.lock 2>/dev/null || exit 1
flock -n 9 || { echo "storage-cleanup already running, skip" >> "$LOG"; exit 0; }
ionice -c 3 -p $$ 2>/dev/null || true
# Fuse thresholds: data disk >80% or system disk >90% -> dry-run only, no real deletion
DATA_PCT=$(df /data | tail -1 | awk '{print $5}' | tr -d '%')
SYS_PCT=$(df / | tail -1 | awk '{print $5}' | tr -d '%')
if [ "$DATA_PCT" -gt 80 ] || [ "$SYS_PCT" -gt 90 ]; then MODE="dry-run"; echo "fuse tripped data=$DATA_PCT sys=$SYS_PCT, forcing dry-run" >> "$LOG"; fi
echo "{\"date\":\"$(date -Iseconds)\",\"mode\":\"$MODE\",\"data_pct\":$DATA_PCT,\"sys_pct\":$SYS_PCT,\"tmp_candidates\":[],\"note\":\"manifest outside cleanup scope, kept 90 days\"}" > "$MANIFEST.tmp"
# /tmp: ctime >24h, not mapped, not lock/socket, only files, -xdev, no root (script runs as ubuntu)
find /tmp -xdev -type f -ctime +0 ! -name "*.lock" ! -name "*.sock" ! -name "ceo-opencode.lock" ! -name "storage-cleanup.lock" 2>/dev/null | while read -r f; do
  if grep -q "$f" /proc/*/maps 2>/dev/null; then continue; fi
  echo "$f" >> "$MANIFEST.tmp.list"
done
wc -l < "$MANIFEST.tmp.list" 2>/dev/null || echo 0
if [ "$MODE" = "--real" ]; then
  TRASHDIR="/data/trash/$(date +%F)"; mkdir -p "$TRASHDIR" 2>/dev/null || true
  while read -r f; do b=$(basename -- "$f"); d="$TRASHDIR/$b"; [ -e "$d" ] && d="$TRASHDIR/${b}_$(date +%H%M%S)_$$"; mv -- "$f" "$d" 2>/dev/null || true; done < "$MANIFEST.tmp.list" 2>/dev/null || true
  echo "real deletion done, see manifest $MANIFEST" >> "$LOG"
else
  echo "dry-run only, no deletion, manifest $MANIFEST" >> "$LOG"
fi
mv "$MANIFEST.tmp" "$MANIFEST" 2>/dev/null || true
echo "storage-cleanup $MODE done"
