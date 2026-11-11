#!/bin/bash
# worker_pull_stage_b.sh — B段：枢纽拉回并校验（2026-10-28，两段式拉取）
# 用法：worker_pull_stage_b.sh <通道暂存目录> <目标目录> <来源主机> <通道名>
# 通道暂存目录在生产中由 InstaBox exec 拉取填充，本脚本不直接连工人机、不改工人机。
# 逐文件校验 sha256；目标已存在且内容不同（未批准覆盖）时整单拒绝、不覆盖、不写 pull-source.json。
set -euo pipefail
SRCDIR="${1:?用法：stage_b.sh <通道暂存目录> <目标目录> <来源主机> <通道名>}"
DESTDIR="${2:?用法：stage_b.sh <通道暂存目录> <目标目录> <来源主机> <通道名>}"
HOST="${3:?用法：stage_b.sh <通道暂存目录> <目标目录> <来源主机> <通道名>}"
CHANNEL="${4:?用法：stage_b.sh <通道暂存目录> <目标目录> <来源主机> <通道名>}"
[ -f "$SRCDIR/manifest.jsonl" ] || { echo "无 manifest：$SRCDIR/manifest.jsonl" >&2; exit 2; }
mkdir -p "$DESTDIR"
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
# 先校验全部，再落盘：任一 sha 不符或未批准覆盖即整单退出
while IFS= read -r line; do
  file=$(printf '%s' "$line" | sed -n 's/.*"file":"\([^"]*\)".*/\1/p')
  sha=$(printf '%s' "$line" | sed -n 's/.*"sha256":"\([^"]*\)".*/\1/p')
  [ -f "$SRCDIR/$file" ] || { echo "暂存缺文件：$file" >&2; exit 3; }
  actual=$(sha256sum "$SRCDIR/$file" | cut -d' ' -f1)
  [ "$actual" = "$sha" ] || { echo "sha 不符：$file 期望 $sha 实测 $actual" >&2; exit 4; }
  if [ -f "$DESTDIR/$file" ]; then
    existing=$(sha256sum "$DESTDIR/$file" | cut -d' ' -f1)
    [ "$existing" = "$sha" ] || { echo "拒绝覆盖未批准文件：$DESTDIR/$file" >&2; exit 5; }
  fi
done < "$SRCDIR/manifest.jsonl"
while IFS= read -r line; do
  file=$(printf '%s' "$line" | sed -n 's/.*"file":"\([^"]*\)".*/\1/p')
  cp -p "$SRCDIR/$file" "$DESTDIR/$file"
done < "$SRCDIR/manifest.jsonl"
files_json=$(python3 -c "import json,sys; print(json.dumps([json.loads(l)['file'] for l in open(sys.argv[1])]))" "$SRCDIR/manifest.jsonl")
printf '{"source_host":"%s","channel":"%s","time":"%s","files":%s}\n' "$HOST" "$CHANNEL" "$TS" "$files_json" > "$DESTDIR/pull-source.json"
echo "stage_b ok: files -> $DESTDIR, pull-source.json written"
