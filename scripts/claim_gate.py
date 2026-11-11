#!/usr/bin/env python3
"""认知锁死：架构类断言出口前必须 grep 真实配置。
用法：python3 claim_gate.py --claim "大管家模型是Zen" --evidence /home/ubuntu/.config/opencode/opencode.json:deepseek-v4.1-flash-free
规则：任一 evidence 文件不存在，或文件里 grep 不到关键词，即 exit 2，输出“未核实”，禁止把该断言发给董事长。
本地文件路径须先经 ssh 读取到本地证据副本，或直接在枢纽跑本脚本；本机版只认已下载的证据文件，不认口头说法。
"""
import argparse, sys, pathlib
ap = argparse.ArgumentParser()
ap.add_argument("--claim", required=True); ap.add_argument("--evidence", action="append", required=True)
a = ap.parse_args()
for ev in a.evidence:
    if ":" not in ev:
        print("未核实：证据格式必须是 文件:关键词"); sys.exit(2)
    path, kw = ev.rsplit(":", 1)
    p = pathlib.Path(path)
    if not p.exists():
        print(f"未核实：配置文件不存在 {path}，断言“{a.claim}”禁止发送"); sys.exit(2)
    if kw not in p.read_text(encoding="utf-8", errors="ignore"):
        print(f"未核实：{path} 里 grep 不到 {kw}，断言“{a.claim}”禁止发送"); sys.exit(2)
print(f"核实通过：断言“{a.claim}”有配置证据")
