#!/usr/bin/env bash
# 儿童项目资源租约（child-lease）cgroup 固化脚本
# 方案与回滚：/home/ubuntu/ai-team/state/child-lease-persistence-plan-20261023.md
# 用法：child_lease_cgroup.sh [apply|enforce|check]
#   apply   开机建租约并写限额（幂等）
#   enforce apply + 按白名单把漏出租约的儿童进程迁回
#   check   只回读校验
# 白名单（儿童项目改路径/启动名时须同步此处）：
#   /data/p0a                             + streaming_server.py
#   /home/ubuntu/child-companion/voice-app + app.py

set -euo pipefail

CG=/sys/fs/cgroup/child-lease
CPU_MAX="120000 100000"
MEM_MAX="5905580032"
MODE="${1:-apply}"

ensure_controllers() {
    local want
    for want in cpu memory; do
        if ! grep -qw "$want" /sys/fs/cgroup/cgroup.subtree_control; then
            echo "+$want" > /sys/fs/cgroup/cgroup.subtree_control
        fi
    done
}

apply_limits() {
    mkdir -p "$CG"
    echo "$CPU_MAX" > "$CG/cpu.max"
    echo "$MEM_MAX" > "$CG/memory.max"
}

verify_limits() {
    local cpu mem
    cpu=$(cat "$CG/cpu.max")
    mem=$(cat "$CG/memory.max")
    if [ "$cpu" != "$CPU_MAX" ] || [ "$mem" != "$MEM_MAX" ]; then
        echo "FAIL child-lease cpu.max='$cpu' memory.max='$mem'"
        exit 1
    fi
    echo "OK child-lease cpu.max='$cpu' memory.max='$mem'"
}

in_lease() {
    grep -qx "/child-lease" "/proc/$1/cgroup" 2>/dev/null
}

enforce_members() {
    local pid cwd cmd moved=0
    for pid in /proc/[0-9]*; do
        pid=${pid#/proc/}
        [ -r "/proc/$pid/cmdline" ] || continue
        cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null || true)
        case "$cwd" in
            /data/p0a|/home/ubuntu/child-companion/voice-app) ;;
            *) continue ;;
        esac
        cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
        case "$cmd" in
            *streaming_server.py*|*app.py*) ;;
            *) continue ;;
        esac
        if ! in_lease "$pid"; then
            if echo "$pid" > "$CG/cgroup.procs" 2>/dev/null; then
                echo "moved pid=$pid cwd=$cwd"
                moved=$((moved + 1))
            else
                echo "WARN failed to move pid=$pid"
            fi
        fi
    done
    echo "enforce done moved=$moved"
}

case "$MODE" in
    apply)
        ensure_controllers
        apply_limits
        verify_limits
        ;;
    enforce)
        ensure_controllers
        apply_limits
        verify_limits
        enforce_members
        ;;
    check)
        verify_limits
        ;;
    *)
        echo "usage: $0 [apply|enforce|check]" >&2
        exit 2
        ;;
esac
