#!/bin/bash
# worker_pull_stage_a.sh — A段：主工本机出产物（2026-10-28，两段式拉取）
# 用法：worker_pull_stage_a.sh <产物目录> <来源主机> <产品目录>
# 产物目录内生成产品文件副本 + manifest.jsonl（文件、sha256、时间、来源主机）。
# 只读产品目录，不改产品源；不接生产、不派单、不改 cron。
set -euo pipefail
OUTDIR="${1:?用法：stage_a.sh <产物目录> <来源主机> <产品目录>}"
HOST="${2:?用法：stage_a.sh <产物目录> <来源主机> <产品目录>}"
PRODDIR="${3:?用法：stage_a.sh <产物目录> <来源主机> <产品目录>}"
[ -d "$PRODDIR" ] || { echo "产品目录不存在：$PRODDIR" >&2; exit 2; }
mkdir -p "$OUTDIR"
: > "$OUTDIR/manifest.jsonl"
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
for f in "$PRODDIR"/*; do
  [ -f "$f" ] || continue
  base=$(basename "$f")
  cp -p "$f" "$OUTDIR/$base"
  sha=$(sha256sum "$OUTDIR/$base" | cut -d' ' -f1)
  printf '{"file":"%s","sha256":"%s","time":"%s","source_host":"%s"}\n' "$base" "$sha" "$TS" "$HOST" >> "$OUTDIR/manifest.jsonl"
done
[ -s "$OUTDIR/manifest.jsonl" ] || { echo "产品目录无文件：$PRODDIR" >&2; exit 3; }
echo "stage_a ok: $(wc -l < "$OUTDIR/manifest.jsonl") files -> $OUTDIR"
