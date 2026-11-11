#!/usr/bin/env bash
set -euo pipefail
TEAM_ROOT="${TEAM_ROOT:-/home/ubuntu/ai-team}"
TASK_ID="$1"
BUNDLE="$TEAM_ROOT/inbox/$TASK_ID"
CONTRACT="$TEAM_ROOT/contracts/$TASK_ID.json"
OUT=$(python3 "$TEAM_ROOT/scripts/verify.py" "$BUNDLE" "$CONTRACT" || true)
VERDICT=$(echo "$OUT" | python3 -c 'import sys,json; print(json.load(sys.stdin)["verdict"])')
echo "$OUT" > "$TEAM_ROOT/logs/verify-$TASK_ID.json"

# 2026-10-31 OPT 校验闸并行记账：同调 gate_authority，只记结果不拦截
GATE_OF=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('output_file',''))" "$CONTRACT" 2>/dev/null || true)
if [ -n "$GATE_OF" ] && [ -f "$BUNDLE/$GATE_OF" ]; then
  bash "$TEAM_ROOT/scripts/gate_authority.sh" "$CONTRACT" "$BUNDLE/$GATE_OF" > "$TEAM_ROOT/logs/gate_parallel-$TASK_ID.log" 2>&1 || true
else
  echo "gate_parallel_skip no_deliverable_or_no_output_file" > "$TEAM_ROOT/logs/gate_parallel-$TASK_ID.log"
fi

case "$VERDICT" in
  PASS)
    # L3 专家评审门禁
    REVIEW_OUT=$(python3 "$TEAM_ROOT/scripts/review_tristate.py" "$BUNDLE" "$TASK_ID" 2>/dev/null || echo '{"verdict":"BLOCKED","reasons":["review_tristate script error"]}')
    REVIEW_VERDICT=$(echo "$REVIEW_OUT" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("verdict","BLOCKED"))' 2>/dev/null || echo "BLOCKED")
    echo "$REVIEW_OUT" > "$TEAM_ROOT/logs/review-$TASK_ID.json"
    case "$REVIEW_VERDICT" in
      PASS)
        mkdir -p "$TEAM_ROOT/knowledge/$TASK_ID"
        cp -r "$BUNDLE/artifacts/." "$TEAM_ROOT/knowledge/$TASK_ID/" 2>/dev/null || true
        echo "INGESTED $TASK_ID" ;;
      REVISE)
        mkdir -p "$TEAM_ROOT/quarantine/$TASK_ID"
        mv "$BUNDLE" "$TEAM_ROOT/quarantine/$TASK_ID/bundle" 2>/dev/null || true
        echo "$REVIEW_OUT" > "$TEAM_ROOT/quarantine/$TASK_ID/review.json"
        echo "QUARANTINED $TASK_ID verdict=REVISE" ;;
      BLOCKED|*)
        mkdir -p "$TEAM_ROOT/quarantine/$TASK_ID"
        mv "$BUNDLE" "$TEAM_ROOT/quarantine/$TASK_ID/bundle" 2>/dev/null || true
        echo "$REVIEW_OUT" > "$TEAM_ROOT/quarantine/$TASK_ID/review.json"
        echo "QUARANTINED $TASK_ID verdict=BLOCKED NEED_HUMAN" ;;
    esac ;;
  REJECT|NEED_HUMAN)
    mkdir -p "$TEAM_ROOT/quarantine/$TASK_ID"
    mv "$BUNDLE" "$TEAM_ROOT/quarantine/$TASK_ID/bundle" 2>/dev/null || mv "$BUNDLE" "$TEAM_ROOT/quarantine/$TASK_ID/bundle-$(date +%Y%m%d-%H%M%S)-$$" 2>/dev/null || true
    echo "$OUT" > "$TEAM_ROOT/quarantine/$TASK_ID/verdict.json"
    echo "QUARANTINED $TASK_ID verdict=$VERDICT" ;;
esac

# Append-only audit record (supervisor item 4/8): one line per ingest, never rewritten.
python3 -c 'import json,sys,time; rec={"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "task_id": sys.argv[1], "verify": sys.argv[2], "review": sys.argv[3]}; open(sys.argv[4], "a").write(json.dumps(rec, ensure_ascii=False) + "\n")' "$TASK_ID" "$VERDICT" "${REVIEW_VERDICT:-none}" "$TEAM_ROOT/logs/review_audit.jsonl" || true
