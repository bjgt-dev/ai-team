#!/usr/bin/env python3
"""大管家自动化自省：打回/新隐患自动写规则库，不是只写反思日志。
用法：python3 auto_rule_update.py --signature <触发签名> --rule <拦截规则> [--lib <路径>]
行为：签名已存在则不重复写（返回exists），新签名则追加并返回added；规则库为JSON列表。
"""
import argparse, json, os, sys
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--signature", required=True)
    ap.add_argument("--rule", required=True)
    ap.add_argument("--lib", default="/home/ubuntu/ai-team/state/rule-library.json")
    a = ap.parse_args()
    try:
        lib = json.load(open(a.lib, encoding="utf-8"))
    except Exception:
        lib = []
    if any(r.get("signature") == a.signature for r in lib):
        print("exists:" + a.signature); return 0
    lib.append({"signature": a.signature, "rule": a.rule})
    os.makedirs(os.path.dirname(a.lib), exist_ok=True)
    with open(a.lib, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False, indent=2)
    print("added:" + a.signature + " total=" + str(len(lib))); return 0
if __name__ == "__main__":
    sys.exit(main())
