#!/usr/bin/env python3
# 每日清理 /tmp 下陈旧的 .3*.so 临时库文件（opencode/chrome 每次运行都会生成，不清理会吃满磁盘）。
# 安全规则（2026-10-30收紧后放宽）：只删 mtime 早于1小时前的，且排除正被任何进程 mmap 的文件；正在用的不碰。昨日规则在85%时删不动（当日文件全被保护），故改1小时宽限。
import glob, os, re, time
from datetime import datetime, timedelta
mapped = set()
for pid in os.listdir('/proc'):
    if not pid.isdigit():
        continue
    try:
        with open(f'/proc/{pid}/maps') as fh:
            maps = fh.read()
    except Exception:
        continue
    for m in re.finditer(r'/tmp/\.3[^\s]+\.so', maps):
        mapped.add(m.group(0))
yesterday = (datetime.now() - timedelta(hours=1)).timestamp()  # 1小时宽限，非昨日
deleted = freed = 0
for f in glob.glob('/tmp/.3*.so'):
    if f in mapped:
        continue
    try:
        if os.path.getmtime(f) < yesterday:
            freed += os.path.getsize(f)
            os.unlink(f)
            deleted += 1
    except Exception:
        pass
print(f'tmp-so-cleanup deleted={deleted} freed_GB={freed/1024**3:.2f}')
