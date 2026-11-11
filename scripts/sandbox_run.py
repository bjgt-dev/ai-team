#!/usr/bin/env python3
"""沙盒跑：只在隔离目录执行，命令写死白名单（echo/cat/ls），不接受任意 shell。
用法：sandbox_run.py <task> <echo|cat|ls> <arg>
"""
import sys, pathlib, subprocess
task, cmd, arg = sys.argv[1], sys.argv[2], sys.argv[3]
d = pathlib.Path(f"/tmp/harness-sandbox-{task}"); d.mkdir(parents=True, exist_ok=True)
if cmd not in ("echo","cat","ls"):
    print("SANDBOX_REFUSE: command not whitelisted"); sys.exit(1)
r = subprocess.run([cmd, arg] if cmd != "echo" else ["echo", arg], capture_output=True, text=True, cwd=d, timeout=10)
print("SANDBOX_OUTPUT:", r.stdout.strip()[:200]); print("SANDBOX_PASS" if r.returncode==0 else "SANDBOX_FAIL")
