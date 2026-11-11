#!/usr/bin/env bash
# P0-1 single-writer gateway control (2026-10-23)
# Why: OpenClaw state ownership is keyed by container hostname/PID namespace.
# A stopped container leaves a stale gateway.state.lock; a new container must
# wait for the 90s heartbeat lease to expire before it may take ownership.
# This wrapper serializes all gateway lifecycle operations on the host.
set -euo pipefail

CONTAINER="${OPENCLAW_GATEWAY_CONTAINER:-openclaw-official-openclaw-gateway-1}"
STATE_DIR="${OPENCLAW_STATE_DIR:-/opt/openclaw-official/.openclaw}"
LOCK_FILE="$STATE_DIR/tmp/openclaw-1000/gateway.state.lock"
CTL_LOCK="/home/ubuntu/ai-team/state/locks/gateway-ctl.lock"
RESTART_LOG="/home/ubuntu/ai-team/state/gateway-restart.log"
OWNER_STALE_SECONDS="${OWNER_STALE_SECONDS:-95}"
HEALTH_WAIT_SECONDS="${HEALTH_WAIT_SECONDS:-600}"

mkdir -p "$(dirname "$CTL_LOCK")" "$(dirname "$RESTART_LOG")"

log() {
  printf '%s %s\n' "$(date -Is)" "$*" | tee -a "$RESTART_LOG"
}

docker_() {
  sudo -n docker "$@"
}

container_state() {
  docker_ inspect --format '{{.State.Status}}' "$CONTAINER" 2>/dev/null || true
}

health_state() {
  docker_ inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER" 2>/dev/null || true
}

wait_owner_release() {
  local deadline=$((SECONDS + 150))
  while [ -e "$LOCK_FILE" ]; do
    local mtime age
    mtime=$(sudo -n stat -c %Y "$LOCK_FILE" 2>/dev/null || echo 0)
    age=$(( $(date +%s) - mtime ))
    if [ "$age" -ge "$OWNER_STALE_SECONDS" ]; then
      log "OWNER_RELEASED age=${age}s path=$LOCK_FILE"
      return 0
    fi
    if [ "$SECONDS" -ge "$deadline" ]; then
      log "OWNER_WAIT_TIMEOUT age=${age}s path=$LOCK_FILE"
      return 1
    fi
    sleep 3
  done
  log "OWNER_RELEASED lock=absent path=$LOCK_FILE"
}

wait_healthy() {
  local deadline=$((SECONDS + HEALTH_WAIT_SECONDS))
  while [ "$SECONDS" -lt "$deadline" ]; do
    local h
    h=$(health_state)
    if [ "$h" = "healthy" ]; then
      log "HEALTHY state=$h"
      return 0
    fi
    sleep 5
  done
  log "UNHEALTHY state=$(health_state)"
  return 1
}

do_restart() {
  local reason="${1:-manual}"
  exec 9>"$CTL_LOCK"
  if ! flock -w 30 9; then
    log "ABORT reason=$reason ctl_lock_busy"
    return 75
  fi
  if ! docker_ inspect "$CONTAINER" >/dev/null 2>&1; then
    log "ABORT reason=$reason container_missing container=$CONTAINER"
    return 2
  fi
  log "RESTART_BEGIN reason=$reason container=$CONTAINER state=$(container_state)"
  if [ "$(container_state)" = "running" ]; then
    docker_ stop -t 30 "$CONTAINER" >/dev/null
    log "STOPPED reason=$reason"
  else
    log "STOP_SKIP reason=$reason state=$(container_state)"
  fi
  wait_owner_release
  log "START reason=$reason"
  docker_ start "$CONTAINER" >/dev/null
  wait_healthy
  log "RESTART_DONE reason=$reason state=$(container_state) health=$(health_state)"
}

do_status() {
  printf 'container=%s\n' "$CONTAINER"
  printf 'state=%s\n' "$(container_state)"
  printf 'health=%s\n' "$(health_state)"
  printf 'owner_lock=%s\n' "$LOCK_FILE"
  if [ -e "$LOCK_FILE" ]; then
    sudo -n stat -c 'owner_lock_mtime=%y owner_lock_size=%s' "$LOCK_FILE" || true
  fi
  sudo -n docker stats --no-stream --format 'rss={{.MemUsage}} cpu={{.CPUPerc}}' "$CONTAINER" 2>/dev/null || true
}

case "${1:-}" in
  restart) do_restart "${2:-manual}" ;;
  status) do_status ;;
  *)
    echo "usage: $0 {restart [reason]|status}" >&2
    exit 64
    ;;
esac
