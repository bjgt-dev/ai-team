#!/usr/bin/env python3
"""eval-caseb-watch-20261111.py — Case B（EVAL-005）产物替换监视器（跑在枢纽上）。

在 worker 的 /v1/artifact 与 /v1/submit 之间做原子替换：
  - 监视 state/deliverables/<task_uuid>.bin（artifact 处理器先写 quarantine、再写 deliverables，
    deliverables 出现即两处都已原子落盘）；
  - 文件出现且大小连续两次轮询稳定（10ms 间隔）后，立即用 os.replace 把两处文件都换成篡改包；
  - 记录各时间点（artifact 出现、大小稳定、替换完成）与替换前后 sha。
用法：python3 scripts/eval-caseb-watch-20261111.py --task-uuid <uuid> --tampered <篡改包路径> [--timeout-min 25]
退出码：0 = 替换成功；1 = 超时未见 artifact 或替换失败。
"""
import argparse
import hashlib
import json
import os
import sys
import time

BASE = "/home/ubuntu/ai-team"
QUARANTINE_DIR = os.path.join(BASE, "state", "queue-api-20261105", "quarantine")
DELIV_DIR = os.path.join(BASE, "state", "deliverables")


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-uuid", required=True)
    ap.add_argument("--tampered", required=True)
    ap.add_argument("--timeout-min", type=int, default=25)
    a = ap.parse_args()
    with open(a.tampered, "rb") as f:
        tampered_bytes = f.read()
    tampered_sha = hashlib.sha256(tampered_bytes).hexdigest()
    qpath = os.path.join(QUARANTINE_DIR, a.task_uuid + ".bin")
    dpath = os.path.join(DELIV_DIR, a.task_uuid + ".bin")
    log = {"task_uuid": a.task_uuid, "tampered_sha256": tampered_sha,
           "tampered_bytes": len(tampered_bytes), "events": []}
    t0 = time.time()
    deadline = t0 + a.timeout_min * 60
    seen_size = None
    stable_since = None
    while time.time() < deadline:
        if os.path.exists(dpath):
            size = os.path.getsize(dpath)
            now = time.time()
            if seen_size is None:
                seen_size = size
                stable_since = now
                log["events"].append({"event": "artifact_seen", "t": now - t0,
                                      "size": size})
                print("WATCH artifact_seen size=%d t=%.3fs" % (size, now - t0), flush=True)
            elif size == seen_size:
                if now - stable_since >= 0.02:
                    # 大小稳定 20ms：两处都已落盘，立即替换
                    before_q = _sha(qpath) if os.path.exists(qpath) else None
                    before_d = _sha(dpath)
                    for target in (qpath, dpath):
                        tmp = target + ".swap.%d" % os.getpid()
                        with open(tmp, "wb") as f:
                            f.write(tampered_bytes)
                        os.replace(tmp, target)
                    t_swap = time.time()
                    after_q = _sha(qpath)
                    after_d = _sha(dpath)
                    log["events"].append({"event": "swapped", "t": t_swap - t0,
                                         "before_quarantine_sha256": before_q,
                                         "before_deliverables_sha256": before_d,
                                         "after_quarantine_sha256": after_q,
                                         "after_deliverables_sha256": after_d})
                    ok = after_q == tampered_sha and after_d == tampered_sha
                    print("WATCH swapped t=%.3fs ok=%s before_d=%s"
                          % (t_swap - t0, ok, (before_d or "")[:16]), flush=True)
                    print(json.dumps(log, ensure_ascii=False))
                    return 0 if ok else 1
            else:
                seen_size = size
                stable_since = now
        time.sleep(0.01)
    print("WATCH_FAIL timeout %.1f min, no artifact for %s" % (a.timeout_min, a.task_uuid))
    print(json.dumps(log, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    sys.exit(main())
