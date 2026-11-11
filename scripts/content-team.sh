#!/bin/sh
# 内容流水线入口（董事长令 2026-10-20）：岗位1 事实稿（契约+门禁产出）→ 岗位2 去AI味主编 → 岗位3 小红书排版+生图Prompt
# 用法：content-team.sh <初稿.md> <输出目录>
set -e
DIR=$(dirname "$0")
exec python3 "$DIR/content_team.py" "$1" "$2"
