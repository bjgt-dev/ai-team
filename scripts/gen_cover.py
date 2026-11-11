#!/usr/bin/env python3
# gen_cover.py — 小红书封面图生成（image-generation 技能的执行器，2026-10-20 同化落地）
# 主路线：Pollinations（免费、无 key、仅出站 GET 提示词文本）；失败重试 1 次，再失败退出码 2（调用方走无图降级）
# 用法：gen_cover.py --prompt "<生图提示词>" --out <输出.jpg> [--size 1024]
# 输出：一行 JSON {"ok":true,"path":...,"bytes":...,"engine":"pollinations"}；key/凭据：无（本脚本不接触任何凭据）
import argparse, json, sys, time, urllib.parse, urllib.request

def fetch(prompt, size):
    q = urllib.parse.quote(prompt)
    url = (f"https://image.pollinations.ai/prompt/{q}"
           f"?width={size}&height={size}&nologo=true")
    req = urllib.request.Request(url, headers={"User-Agent": "ai-team/gen_cover"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", type=int, default=1024)
    a = ap.parse_args()
    last_err = ""
    for attempt in (1, 2):
        try:
            raw = fetch(a.prompt, a.size)
            if raw[:3] == b"\xff\xd8\xff" and len(raw) > 10000:
                with open(a.out, "wb") as f:
                    f.write(raw)
                print(json.dumps({"ok": True, "path": a.out, "bytes": len(raw),
                                  "engine": "pollinations", "attempt": attempt}))
                return
            last_err = f"bad payload: {len(raw)} bytes, magic {raw[:3].hex()}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        if attempt == 1:
            time.sleep(5)
    print(json.dumps({"ok": False, "error": last_err}))
    sys.exit(2)

if __name__ == "__main__":
    main()
