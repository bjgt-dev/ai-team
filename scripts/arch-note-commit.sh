#!/bin/bash
# 每轮札记追加后快照提交（裁决 inc-20261108 / inc-20261110-2nd 选项 A 落地机制）。
# 用法：bash scripts/arch-note-commit.sh（由写札记的班次在 edit 追加后调用）。
set -e
cd /home/ubuntu/ai-team
git add -f ceo-seat/out/butler-arch-note.md
if git diff --cached --quiet; then echo "no changes, nothing to commit"; exit 0; fi
git commit -qm "arch-note round snapshot $(date +%F_%H%M)"
git log --oneline -1
