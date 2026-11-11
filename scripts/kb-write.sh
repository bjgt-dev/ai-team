#!/bin/bash
# kb-write.sh v2 — 写知识库（实时写；目录重组后默认进收件箱）
# 用法：
#   kb-write.sh <文件名.md> <本地文件路径>     # 写进枢纽 knowledge/收件箱/<文件名>
#   kb-write.sh <领域/文件名.md> <本地文件路径> # 写进指定领域，如 工具/ops-foo.md
#   echo "内容" | kb-write.sh <文件名.md> -      # 从 stdin 读内容写进枢纽 knowledge/
# 机制：scp 到枢纽 knowledge/（默认收件箱），随即核对 sha（写完立刻能查，
# kb-ask.sh 的直接 grep 路会马上命中，不用等索引重建）。文件名须以 .md 结尾，
# 不许含 ..（防目录穿越），不许以 / 开头。写完后由人工或定期任务从收件箱
# 归档到对应领域；新条目进收件箱即算入库完成。
set -u
if [ $# -lt 2 ]; then
  echo "用法：kb-write.sh <文件名.md|[领域/]文件名.md> <本地文件|-（stdin）>" >&2
  exit 2
fi
NAME="$1"
SRC="$2"
case "$NAME" in
  *.md) ;;
  *) echo "文件名须以 .md 结尾：$NAME" >&2; exit 2;;
esac
case "$NAME" in
  *..*|/*) echo "文件名不许含 .. 或以 / 开头：$NAME" >&2; exit 2;;
esac
# 定落点：带 / 的按给定领域，不带的默认进收件箱
case "$NAME" in
  */*) DEST="$NAME" ;;
  *) DEST="收件箱/$NAME" ;;
esac
# 准备本地临时文件
TMP=""
if [ "$SRC" = "-" ]; then
  TMP=$(mktemp /tmp/kb-write-XXXXXX.md)
  cat > "$TMP"
  SRC="$TMP"
fi
if [ ! -f "$SRC" ]; then
  echo "源文件不存在：$SRC" >&2
  [ -n "$TMP" ] && rm -f "$TMP"
  exit 2
fi
SHA_LOCAL=$(sha256sum "$SRC" | awk '{print $1}')
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=30"
DEST_DIR=$(dirname "$DEST")
ssh $SSH_OPTS arm-agent "mkdir -p ~/ai-team/knowledge/$DEST_DIR" 2>&1 | grep -v "^$" || true
scp $SSH_OPTS "$SRC" "arm-agent:~/ai-team/knowledge/$DEST" 2>&1
if [ $? -ne 0 ]; then
  echo "scp 写入失败：$DEST" >&2
  [ -n "$TMP" ] && rm -f "$TMP"
  exit 1
fi
SHA_HUB=$(ssh $SSH_OPTS arm-agent "sha256sum ~/ai-team/knowledge/$DEST" 2>&1 | awk '{print $1}')
[ -n "$TMP" ] && rm -f "$TMP"
if [ "$SHA_LOCAL" = "$SHA_HUB" ]; then
  echo "OK 已写入枢纽 knowledge/$DEST sha=$SHA_HUB（实时可查）"
  exit 0
else
  echo "写入后 sha 不一致：本地 $SHA_LOCAL 枢纽 $SHA_HUB" >&2
  exit 1
fi
