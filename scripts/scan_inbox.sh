#!/usr/bin/env bash
# Scan evidence bundles; only contract location/archiving is handled here.
# Verifier and ingest verdict rules are unchanged.
set -euo pipefail
TEAM_ROOT="${TEAM_ROOT:-/home/ubuntu/ai-team}"
LOG="$TEAM_ROOT/logs/scan_inbox.log"

for bundle_dir in "$TEAM_ROOT/inbox"/*/; do
  [ -d "$bundle_dir" ] || continue
  task_id=$(basename "$bundle_dir")
  contract="$TEAM_ROOT/contracts/$task_id.json"
  pending_contracts=()
  for pending_dir in "$TEAM_ROOT/contracts/pending/cue" "$TEAM_ROOT/contracts/pending/muse" "$TEAM_ROOT/contracts/pending"; do
    candidate="$pending_dir/$task_id.json"
    [ ! -f "$candidate" ] || pending_contracts+=("$candidate")
  done

  # Resolve an existing historical contract or a pending source, without
  # removing pending contracts before a terminal verdict exists.
  reference="$contract"
  if [ ! -f "$reference" ] && [ "${#pending_contracts[@]}" -gt 0 ]; then
    reference="${pending_contracts[0]}"
  fi
  if [ ! -f "$reference" ]; then
    # Fallback (2026-10-20 stress-test fix): a fast-claimed task has already left
    # pending/ when its bundle arrives; resolve the contract from claimed/ or
    # done/ instead of orphaning a complete bundle.
    for cand in "$TEAM_ROOT/contracts/claimed"/*/"$task_id.json" "$TEAM_ROOT/contracts/done/$task_id.json"; do
      if [ -f "$cand" ]; then reference="$cand"; break; fi
    done
  fi
  if [ ! -f "$reference" ]; then
    # Existing orphan behavior, now only after checking all three locations.
    mkdir -p "$TEAM_ROOT/quarantine/orphan-$task_id"
    mv "$bundle_dir" "$TEAM_ROOT/quarantine/orphan-$task_id/bundle"
    echo "$(date -Is) ORPHAN $task_id moved to quarantine/orphan-$task_id" >> "$LOG"
    continue
  fi

  # Ambiguous task IDs must not silently select another executor's contract.
  conflict=0
  for candidate in "${pending_contracts[@]}"; do
    if ! cmp -s "$reference" "$candidate"; then conflict=1; break; fi
  done
  if [ "$conflict" -eq 1 ]; then
    echo "$(date -Is) CONTRACT_PATH_CONFLICT $task_id left unchanged" >> "$LOG"
    continue
  fi

  # Existing completeness check; incomplete uploads leave pending intact.
  if [ ! -f "$bundle_dir/manifest.json" ] || [ ! -f "$bundle_dir/receipts.jsonl" ] || [ ! -f "$bundle_dir/acceptance_self_check.json" ]; then
    echo "$(date -Is) INCOMPLETE $task_id skipped (waiting for full bundle)" >> "$LOG"
    continue
  fi

  # ingest.sh requires this canonical path. Stage an identical copy for its
  # unchanged verifier, then archive the pending source only after its verdict.
  staged=0
  if [ ! -f "$contract" ]; then
    cp -p "$reference" "$contract"
    staged=1
  fi
  echo "$(date -Is) INGEST $task_id" >> "$LOG"
  verdict=""
  if bash "$TEAM_ROOT/scripts/ingest.sh" "$task_id" >> "$LOG" 2>&1; then
    verdict=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["verdict"])' "$TEAM_ROOT/logs/verify-$task_id.json" 2>/dev/null || true)
  fi
  case "$verdict" in
    PASS|REJECT|NEED_HUMAN)
      for candidate in "${pending_contracts[@]}"; do
        if [ -f "$candidate" ] && cmp -s "$candidate" "$contract"; then
          mv -f "$candidate" "$contract"
          echo "$(date -Is) CONTRACT_ARCHIVED $task_id verdict=$verdict source=$candidate" >> "$LOG"
        fi
      done
      if [ "$verdict" = "REJECT" ]; then
        # Supervisor item 6: send the contract back to the original worker
        # (or requeue without preference), bounded by max_retries.
        python3 "$TEAM_ROOT/scripts/requeue_rejected.py" "$task_id" >> "$LOG" 2>&1 || true
      fi
      ;;
    *)
      # No terminal verdict: undo only the temporary compatibility copy.
      if [ "$staged" -eq 1 ]; then rm -f "$contract"; fi
      echo "$(date -Is) CONTRACT_NOT_ARCHIVED $task_id (no terminal verdict)" >> "$LOG"
      ;;
  esac
done
