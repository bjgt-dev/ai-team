#!/usr/bin/env bash
# gate_authority: 极简安检机。只认三样：契约文件存在、哈希值对得上、路径在标准路径下。缺一样直接 REFUSE，exit 1。
# 权限归董事长独有：本文件与 output_gate.py、gate.sh 非董事长明令不得修改，改动须同步更新 gate-authority.json 哈希清单。
# 用法：bash gate_authority.sh <契约json> <交付物文件>
set -u
cjson="${1:-}"; dpath="${2:-}"
if [ -z "$cjson" ] || [ -z "$dpath" ]; then echo "REFUSE: missing contract or deliverable"; exit 1; fi
if [ ! -f "$cjson" ]; then echo "REFUSE: contract missing: $cjson"; exit 1; fi
if [ ! -f "$dpath" ]; then echo "REFUSE: deliverable missing: $dpath"; exit 1; fi
case "$dpath" in /home/ubuntu/ai-team/ceo-seat/out/*|/home/hatch/workspace/your_files/*) ;; *) echo "REFUSE: non-standard path: $dpath"; exit 1;; esac
want=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('sha256') or '')" "$cjson" 2>/dev/null)
if [ -z "$want" ]; then echo "REFUSE: contract has no sha256: $cjson"; exit 1; fi
got=$(sha256sum "$dpath" | awk '{print $1}')
if [ "$got" != "$want" ]; then echo "REFUSE: hash mismatch want=$want got=$got"; exit 1; fi
echo "PASS gate_authority: $dpath hash=$got"
