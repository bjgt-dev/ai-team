#!/bin/bash
# crontab-safe-install-20261107.sh -- safe cron installer (MEMORY.md 枢纽crontab被清空事故).
# Hard rule (memory-only until now): ANY cron install MUST read the current
# table first, back it up, then append -- never whole-table overwrite.
#   usage: crontab-safe-install-20261107.sh NEW_LINES_FILE [TAG]
#   - reads `crontab -l` (empty table is OK, warned, not fatal)
#   - backs up to $CRONTAB_BACKUP_DIR/crontab.bak-YYYYMMDD-HHMMSS (default
#     ~/ai-team/state/crontab-backups/)
#   - appends only lines not already present (idempotent; TAG comment lines
#     make a repeated install a no-op)
#   - installs, then verifies the new table contains the new lines
#   - NEVER runs bare `crontab FILE` without the read-backup-append flow
# Exit 0 ok, 1 fail. --self-test runs against a fake `crontab` on PATH.
set -u
BACKUP_DIR="${CRONTAB_BACKUP_DIR:-$HOME/ai-team/state/crontab-backups}"

self_test() {
  local tmp; tmp="$(mktemp -d)"
  mkdir -p "$tmp/bin" "$tmp/backups"
  cat > "$tmp/bin/crontab" <<'EOF'
#!/bin/bash
# fake crontab: stores table in $FAKE_CRONTAB_FILE
if [ "$1" = "-l" ]; then
  if [ -f "$FAKE_CRONTAB_FILE" ]; then cat "$FAKE_CRONTAB_FILE"; else
    echo "no crontab for user" >&2; exit 1; fi
elif [ -n "${1:-}" ]; then cat "$1" > "$FAKE_CRONTAB_FILE"
else echo "usage" >&2; exit 1; fi
EOF
  chmod +x "$tmp/bin/crontab"
  export PATH="$tmp/bin:$PATH" FAKE_CRONTAB_FILE="$tmp/table" CRONTAB_BACKUP_DIR="$tmp/backups"
  printf '0 6 * * * /bin/true # job-a\n' > "$tmp/table"
  printf '30 6 * * * /bin/true # job-b\n' > "$tmp/new.txt"
  local ok=1
  bash "$0" "$tmp/new.txt" "test-tag" >/dev/null 2>&1 || ok=0
  grep -q "job-b" "$tmp/table" || ok=0
  ls "$tmp/backups"/crontab.bak-* >/dev/null 2>&1 || ok=0
  grep -q "job-a" "$tmp/table" || ok=0   # old lines preserved (no wipe)
  local before; before="$(wc -l < "$tmp/table")"
  bash "$0" "$tmp/new.txt" "test-tag" >/dev/null 2>&1 || ok=0
  local after; after="$(wc -l < "$tmp/table")"
  [ "$before" = "$after" ] || ok=0       # second run adds nothing (idempotent)
  rm -f "$tmp/table"                      # no existing table at all
  bash "$0" "$tmp/new.txt" "test-tag" >/dev/null 2>&1 || ok=0
  grep -q "job-b" "$tmp/table" || ok=0   # fresh install still works
  rm -rf "$tmp"
  if [ "$ok" = 1 ]; then echo "SELF-TEST ALL PASS"; return 0; else echo "SELF-TEST FAILURES PRESENT"; return 1; fi
}

if [ "${1:-}" = "--self-test" ]; then self_test; exit $?; fi
if [ $# -lt 1 ] || [ ! -f "$1" ]; then
  echo "usage: $0 NEW_LINES_FILE [TAG]" >&2; exit 1
fi
NEW_LINES="$1"; TAG="${2:-manual-$(date +%Y%m%d)}"
mkdir -p "$BACKUP_DIR"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
# 1. READ current table first (never overwrite blind).
if crontab -l > "$TMP" 2>/dev/null; then
  echo "read current crontab: $(wc -l < "$TMP") lines"
else
  echo "WARN no existing crontab; starting from empty table"
  : > "$TMP"
fi
# 2. BACK UP before any change.
BACKUP="$BACKUP_DIR/crontab.bak-$(date +%Y%m%d-%H%M%S)"
cp "$TMP" "$BACKUP" || { echo "FAIL cannot back up crontab" >&2; exit 1; }
echo "backup: $BACKUP"
# 3. APPEND only lines not already present.
ADDED=0
while IFS= read -r line; do
  [ -z "$line" ] && continue
  if grep -Fqx -- "$line" "$TMP"; then
    echo "skip (already present): $line"
  else
    printf '%s\n' "$line" >> "$TMP"; ADDED=$((ADDED + 1))
  fi
done < "$NEW_LINES"
if [ "$ADDED" -gt 0 ]; then
  printf '# installed by crontab-safe-install-20261107 tag=%s\n' "$TAG" >> "$TMP"
fi
# 4. INSTALL the merged table, then VERIFY.
crontab "$TMP" || { echo "FAIL crontab install failed; backup at $BACKUP" >&2; exit 1; }
VERIFY="$(mktemp)"; trap 'rm -f "$TMP" "$VERIFY"' EXIT
crontab -l > "$VERIFY" 2>/dev/null || { echo "FAIL cannot re-read crontab after install" >&2; exit 1; }
while IFS= read -r line; do
  [ -z "$line" ] && continue
  grep -Fqx -- "$line" "$VERIFY" || { echo "FAIL line missing after install: $line" >&2; exit 1; }
done < "$NEW_LINES"
echo "PASS installed $ADDED new line(s); old lines preserved; backup $BACKUP"
exit 0
