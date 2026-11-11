#!/bin/bash
# restart-user-service.sh — 以 ubuntu 身份重启一个用户级 systemd 服务（供 gatesvc 经 sudo 调用）
# 用法：restart-user-service.sh <action> <unit>
# 只允许 queue-api.service + restart/start/stop/reload；其余一律拒绝。
# 设置用户 bus 环境变量（sudo 上下文里没有 user bus）。
set -u
ACTION="${1:-}"; UNIT="${2:-}"
case "$ACTION" in restart|start|stop|reload) ;; *) echo "action not allowed: $ACTION" >&2; exit 2;; esac
case "$UNIT" in queue-api.service) ;; *) echo "unit not allowed: $UNIT" >&2; exit 2;; esac
export XDG_RUNTIME_DIR=/run/user/1001
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1001/bus
exec systemctl --user "$ACTION" "$UNIT"
