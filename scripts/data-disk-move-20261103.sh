#!/bin/bash
# Move lumo-ref + pw-test from / to /data/relocated (cp -> verify -> delete).
# Stops (exit 1, no delete) on any verification mismatch. Exact paths only,
# no wildcards. No symlinks are created; the old paths are gone after the
# move (both items are orphaned: zero live references on the hub).
set -u
SRC1=/home/ubuntu/lumo-ref; DST1=/data/relocated/lumo-ref; EXP_F1=838; EXP_B1=54224786
SRC2=/home/ubuntu/pw-test; DST2=/data/relocated/pw-test; EXP_F2=222; EXP_B2=18692635
echo "=== df before (bytes) ==="; df -B1 / /data
sudo -n mkdir -p /data/relocated || { echo "FAIL mkdir"; exit 1; }
sudo -n chown ubuntu:ubuntu /data/relocated || { echo "FAIL chown"; exit 1; }
ls -ld /data/relocated
move_one() {
  SRC=$1; DST=$2; EF=$3; EB=$4
  echo "=== moving $SRC ==="
  F0=$(find "$SRC" | wc -l); B0=$(du -sb "$SRC" | cut -f1)
  echo "source files=$F0 bytes=$B0 (expect $EF/$EB)"
  [ "$F0" = "$EF" ] && [ "$B0" = "$EB" ] || { echo "FAIL source baseline drifted, aborting $SRC"; return 1; }
  cp -a "$SRC" "$DST" || { echo "FAIL cp $SRC"; return 1; }
  F1=$(find "$DST" | wc -l); B1=$(du -sb "$DST" | cut -f1)
  echo "copy files=$F1 bytes=$B1"
  [ "$F1" = "$EF" ] && [ "$B1" = "$EB" ] || { echo "FAIL copy mismatch, NOT deleting $SRC"; return 1; }
  if ! diff -r -q "$SRC" "$DST" > /dev/null 2>&1; then echo "FAIL diff mismatch, NOT deleting $SRC"; return 1; fi
  echo "PASS verified identical, deleting source"
  rm -rf "$SRC" || { echo "FAIL rm $SRC"; return 1; }
  [ -e "$SRC" ] && { echo "FAIL source still exists"; return 1; }
  echo "PASS moved $SRC -> $DST"
}
move_one "$SRC1" "$DST1" "$EXP_F1" "$EXP_B1" || exit 1
move_one "$SRC2" "$DST2" "$EXP_F2" "$EXP_B2" || exit 1
echo "=== df after (bytes) ==="; df -B1 / /data; df -h / /data
