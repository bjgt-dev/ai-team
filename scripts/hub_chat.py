#!/usr/bin/env python3
# hub_chat.py - 在枢纽本地调用 newapi 免费池（key 只在本机内存，供跨机调用方经 ssh 使用）
# 用法：python3 hub_chat.py [model] < prompt.txt ；stdout 只输出模型回复正文；空回复退出码 3
import json, sys, urllib.request
sys.path.insert(0, "/home/ubuntu/ai-team/scripts")
import auto_ceo

model = sys.argv[1] if len(sys.argv) > 1 else "foreman-prime"
prompt = sys.stdin.read()
key = auto_ceo.read_key()
if not key:
    print("NO_KEY", file=sys.stderr)
    sys.exit(2)
payload = json.dumps({"model": model,
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 4000}).encode()
req = urllib.request.Request("http://127.0.0.1:3001/v1/chat/completions", data=payload,
                             method="POST", headers={"Authorization": "Bearer " + key,
                                                      "Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=240) as r:
    d = json.loads(r.read().decode())
content = d["choices"][0]["message"]["content"] or ""
if not content.strip():
    print("EMPTY_CONTENT", file=sys.stderr)
    sys.exit(3)
print(content)
