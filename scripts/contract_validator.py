#!/usr/bin/env python3
"""最小权限 + 沙盒：契约限定工具，高危先沙盒。
用法：contract_validator.py --contract <json> --tool <name> --action <high|normal> --sandbox_result <pass|none>
规则：tool 不在 tools_allowed -> REFUSE；action=high 且 sandbox_result!=pass -> REFUSE；否则 ALLOW。
沙盒本身：sandbox_run.py 在 /tmp/harness-sandbox-<task> 隔离目录跑命令，只回输出，不动主环境。
"""
import argparse, json, sys
ap = argparse.ArgumentParser()
ap.add_argument("--contract", required=True); ap.add_argument("--tool", required=True); ap.add_argument("--action", default="normal"); ap.add_argument("--sandbox_result", default="none")
a = ap.parse_args()
c = json.load(open(a.contract))
if a.tool not in c.get("tools_allowed", []):
    print(f"REFUSE: tool {a.tool} not in allowed {c.get('tools_allowed')}"); sys.exit(1)
if a.action == "high" and a.sandbox_result != "pass":
    print("REFUSE: high-risk action without sandbox pass"); sys.exit(1)
print(f"ALLOW tool={a.tool} action={a.action}")
