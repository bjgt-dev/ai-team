#!/bin/sh
# disk_watch v2 (2026-10-24 chairman directive): monitor arm / , arm /data, amd /
# thresholds: 85% each. Alerts -> state/alerts.log + state/alert-disk.md (per-disk sections)
B="${TEAM_ROOT:-/home/ubuntu/ai-team}"
NOW="$(date -Is)"
TMP_MD="$(mktemp)"

check_local() {
  # $1=mount $2=label
  PCT="$(df "$1" | tail -1 | awk '{print $5}' | tr -d %)"
  [ -z "$PCT" ] && return 0
  if [ "$PCT" -ge 85 ]; then
    echo "$NOW DISK_ALERT disk=$2 usage=${PCT}% threshold=85%" >> "$B/state/alerts.log"
    printf '## %s\n- 时间：%s\n- 使用率：%s%%（阈值 85%%）\n\n' "$2" "$NOW" "$PCT" >> "$TMP_MD"
  fi
}

# AMD_HOST: Tailscale IP or hostname of the second node; set AMD_HOST to override.
check_amd() {
  # hub -> amd-proxy via tailscale userspace proxy (see TOOLS.md); best effort
  PCT="$(ssh -o ConnectTimeout=10 -o BatchMode=yes \
    -o ProxyCommand="/var/lib/ocarun/tailscale-bin/tailscale --socket=/tmp/tailscaled-direct.sock nc %h %p" \
    ubuntu@${AMD_HOST:-amd-proxy} "df / | tail -1 | awk '{print \$5}' | tr -d %" 2>/dev/null)"
  [ -z "$PCT" ] && { echo "$NOW DISK_CHECK_FAIL disk=amd unreachable" >> "$B/state/alerts.log"; return 0; }
  if [ "$PCT" -ge 85 ]; then
    echo "$NOW DISK_ALERT disk=amd:/ usage=${PCT}% threshold=85%" >> "$B/state/alerts.log"
    printf '## amd:/\n- 时间：%s\n- 使用率：%s%%（阈值 85%%）\n\n' "$NOW" "$PCT" >> "$TMP_MD"
  fi
}

check_local / "arm:/"
check_local /data "arm:/data"
check_amd

# A4 trash sweep: /data/trash/YYYY-MM-DD dirs older than 7 days, truly delete. Guarded, never other paths.
find /data/trash -mindepth 1 -maxdepth 1 -type d -name "20??-??-??" -mtime +7 -print0 2>/dev/null | xargs -0 -r sudo -n rm -rf 2>/dev/null || true

if [ -s "$TMP_MD" ]; then
  { printf '# 磁盘报警\n\n'; cat "$TMP_MD"; printf '大头排查：docker system df / du -shx /var/lib/containerd /home/ubuntu/*\n'; } > "$B/state/alert-disk.md"
else
  rm -f "$B/state/alert-disk.md"
fi
rm -f "$TMP_MD"
