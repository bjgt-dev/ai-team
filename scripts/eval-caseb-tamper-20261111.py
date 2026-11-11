#!/usr/bin/env python3
"""eval-caseb-tamper-20261111.py — Case B（EVAL-005）篡改 bundle 构造器（跑在枢纽上）。

从与 worker 相同的 5 个种子文件出发，构造一个“manifest 声称原始内容、tar 内 1 个文件被改过”的
PULLONCE-BUNDLE-V1 包：
  - manifest 的 per-file sha256 / bytes 保持原始值（声称原始内容）；
  - bundle_sha256 / bundle_bytes 按篡改后的 tar 重算（否则 parse_bundle 先报外层 mismatch，不含文件路径）；
  - tar 内只改 1 个文件（ladder.py：一处字符串 Red! -> RedX!）。
预期：parse_bundle(tampered) 抛 ValueError "bundle: sha mismatch for ladder.py"；
      parse_bundle(valid) 正常返回 5 个文件。
用法：python3 scripts/eval-caseb-tamper-20261111.py --seed-dir <含5个文件的目录> --out-dir <输出目录>
输出：<out-dir>/tampered-bundle.bin、<out-dir>/valid-bundle.bin、<out-dir>/tamper-record.json
"""
import argparse
import gzip
import hashlib
import io
import json
import os
import sys
import tarfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import artifact_bundle

SEED_FILES = ["immersion_engine.py", "ladder.py", "simulator.py",
              "test_kids4.py", "test_kids5_1.py"]
TAMPER_FILE = "ladder.py"
TAMPER_OLD = b'"Red!"'
TAMPER_NEW = b'"RedX!"'


def _gz_tar(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name in sorted(files):
            data = files[name]
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    orig = {}
    for name in SEED_FILES:
        with open(os.path.join(a.seed_dir, name), "rb") as f:
            orig[name] = f.read()
    if TAMPER_OLD not in orig[TAMPER_FILE]:
        print("TAMPER_FAIL: 篡改锚点 %r 不在 %s 中" % (TAMPER_OLD, TAMPER_FILE))
        return 1
    tampered_files = dict(orig)
    tampered_files[TAMPER_FILE] = orig[TAMPER_FILE].replace(TAMPER_OLD, TAMPER_NEW, 1)
    # 合法包（对照）：与 worker 产出同一构造
    valid = artifact_bundle.build_bundle(orig)
    # 篡改包：manifest 保持原始 per-file sha/bytes，外层 hash 按篡改后重算
    gz = _gz_tar(tampered_files)
    manifest = {"format": "PULLONCE-BUNDLE-V1",
                "files": [{"path": n, "bytes": len(orig[n]),
                           "sha256": hashlib.sha256(orig[n]).hexdigest()}
                          for n in sorted(orig)],
                "bundle_bytes": len(gz),
                "bundle_sha256": hashlib.sha256(gz).hexdigest()}
    header = (b"PULLONCE-BUNDLE-V1\n" +
              json.dumps(manifest, sort_keys=True).encode() + b"\n")
    tampered = header + gz
    # 自验：合法包须解析通过；篡改包须报 per-file 错误（含文件名）
    try:
        got = artifact_bundle.parse_bundle(valid)
        assert sorted(got) == sorted(orig), "valid bundle file list mismatch"
    except Exception as e:
        print("TAMPER_FAIL: 合法包自验失败：%s" % e)
        return 1
    try:
        artifact_bundle.parse_bundle(tampered)
        print("TAMPER_FAIL: 篡改包未被 parse_bundle 拒收（预期 ValueError）")
        return 1
    except ValueError as e:
        if "bundle: sha mismatch for %s" % TAMPER_FILE not in str(e):
            print("TAMPER_FAIL: 篡改包报错不是预期的 per-file 错误：%s" % e)
            return 1
        print("TAMPER_OK: parse_bundle 篡改包报：%s" % e)
    os.makedirs(a.out_dir, exist_ok=True)
    with open(os.path.join(a.out_dir, "valid-bundle.bin"), "wb") as f:
        f.write(valid)
    with open(os.path.join(a.out_dir, "tampered-bundle.bin"), "wb") as f:
        f.write(tampered)
    record = {
        "tamper_file": TAMPER_FILE,
        "tamper_old": TAMPER_OLD.decode(), "tamper_new": TAMPER_NEW.decode(),
        "original_file_sha256": {n: hashlib.sha256(orig[n]).hexdigest() for n in sorted(orig)},
        "tampered_file_sha256": hashlib.sha256(tampered_files[TAMPER_FILE]).hexdigest(),
        "valid_bundle_sha256": hashlib.sha256(valid).hexdigest(),
        "tampered_bundle_sha256": hashlib.sha256(tampered).hexdigest(),
        "valid_bundle_bytes": len(valid),
        "tampered_bundle_bytes": len(tampered),
        "expected_parse_error": "bundle: sha mismatch for %s" % TAMPER_FILE,
    }
    with open(os.path.join(a.out_dir, "tamper-record.json"), "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    print("TAMPER_OK: valid=%dB sha=%s" % (len(valid), record["valid_bundle_sha256"][:16]))
    print("TAMPER_OK: tampered=%dB sha=%s" % (len(tampered), record["tampered_bundle_sha256"][:16]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
