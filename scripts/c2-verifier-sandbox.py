#!/usr/bin/env python3
"""E4 verifier sandbox launcher (cutover51, 2026-11-10).

Isolation actually enforced on this host (unprivileged, no docker, no sudo):
  * kernel netns: one combined unshare(NEWUSER|NEWNET|NEWNS|NEWUTS|NEWIPC)
    -- separate unshare() calls are EPERM on this kernel, and bwrap fails
    for the same reason (uid-map setup / loopback setup). The netns has no
    interfaces up (loopback stays down), so connect() fails ENETUNREACH.
  * write isolation: LD_PRELOAD filter (c2-verifier-sandbox-filter.so)
    denies libc-level write opens outside the workdir with EROFS.
    Kernel-level ro-bind is NOT possible here: every mount() inside an
    unprivileged userns returns EPERM (all types incl. bind/tmpfs,
    proven 2026-11-10). Known limit, disclosed in the filter header:
    raw syscalls / static binaries / children that drop LD_PRELOAD can
    bypass the write filter. Backstop: the verifier diff-checks
    deliverables and forbidden paths after the run and rejects on change.
  * workdir is the only writable tree; HOME/TMPDIR point inside it.

Fail closed: any setup failure exits 125 with SANDBOX_SETUP_FAILED on
stderr; the verifier treats that as a REJECT, never as a plain run.
Usage: c2-verifier-sandbox.py --workdir W -- <argv...>
"""
import os
import signal
import socket
import sys

FILTER_SO = "/home/ubuntu/ai-team/scripts/c2-verifier-sandbox-filter.so"


def main():
    args = sys.argv[1:]
    workdir = None
    argv = []
    i = 0
    while i < len(args):
        if args[i] == "--workdir":
            workdir = args[i + 1]
            i += 2
        elif args[i] == "--":
            argv = args[i + 1:]
            break
        else:
            i += 1
    if not workdir or not argv:
        print("SANDBOX_USAGE: --workdir W -- <argv...>", file=sys.stderr)
        return 125
    if not os.path.isdir(workdir):
        print("SANDBOX_SETUP_FAILED: workdir missing: %s" % workdir, file=sys.stderr)
        return 125
    if not os.path.isfile(FILTER_SO):
        print("SANDBOX_SETUP_FAILED: filter missing: %s" % FILTER_SO, file=sys.stderr)
        return 125
    outer_uid, outer_gid = os.getuid(), os.getgid()
    r1, w1 = os.pipe()
    r2, w2 = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(r1)
            os.close(w2)
            import ctypes
            _libc = ctypes.CDLL("libc.so.6", use_errno=True)
            _libc.prctl(1, 9)  # PR_SET_PDEATHSIG, SIGKILL
            os.unshare(os.CLONE_NEWUSER | os.CLONE_NEWNET | os.CLONE_NEWNS
                       | os.CLONE_NEWUTS | os.CLONE_NEWIPC)
            os.write(w1, b"U")
            os.close(w1)
            if os.read(r2, 1) != b"G":
                os._exit(125)
            os.close(r2)
            # --- self-check: network must be unreachable (kernel) ---
            for host, port in (("8.8.8.8", 53), ("127.0.0.1", 9), (os.environ.get("GATE_BIND_HOST", "127.0.0.1"), 8093)):
                try:
                    socket.create_connection((host, port), timeout=2)
                    raise RuntimeError("selfcheck: connect to %s:%d SUCCEEDED" % (host, port))
                except OSError:
                    pass
            # --- self-check: workdir must be writable ---
            probe = os.path.join(workdir, ".sandbox-probe")
            with open(probe, "w") as f:
                f.write("ok")
            os.unlink(probe)
            os.makedirs(os.path.join(workdir, ".tmp"), exist_ok=True)
            os.environ["HOME"] = workdir
            os.environ["TMPDIR"] = os.path.join(workdir, ".tmp")
            os.environ["LD_PRELOAD"] = FILTER_SO
            os.environ["SANDBOX_WRITABLE"] = workdir
            os.chdir(workdir)
            os.execvp(argv[0], argv)
        except Exception as e:
            try:
                os.write(2, ("SANDBOX_SETUP_FAILED: %s\n" % e).encode())
            except Exception:
                pass
            os._exit(125)
    os.close(w1)
    os.close(r2)
    try:
        msg = os.read(r1, 1)
    except OSError:
        msg = b""
    os.close(r1)
    if msg != b"U":
        try:
            os.waitpid(pid, 0)
        except Exception:
            pass
        print("SANDBOX_SETUP_FAILED: child unshare failed", file=sys.stderr)
        return 125
    try:
        open("/proc/%d/setgroups" % pid, "w").write("deny")
        open("/proc/%d/gid_map" % pid, "w").write("0 %d 1" % outer_gid)
        open("/proc/%d/uid_map" % pid, "w").write("0 %d 1" % outer_uid)
    except OSError as e:
        print("SANDBOX_SETUP_FAILED: idmap write: %s" % e, file=sys.stderr)
        os.write(w2, b"X")
        os.close(w2)
        os.waitpid(pid, 0)
        return 125
    os.write(w2, b"G")
    os.close(w2)

    def _fwd(sig, _frm):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass

    signal.signal(signal.SIGTERM, _fwd)
    signal.signal(signal.SIGINT, _fwd)
    _, status = os.waitpid(pid, 0)
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return os.WEXITSTATUS(status)


if __name__ == "__main__":
    sys.exit(main())
