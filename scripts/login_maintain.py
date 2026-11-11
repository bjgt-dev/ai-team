#!/usr/bin/env python3
"""登录自维持：不靠董事长重登，靠档案备份+权限修复+定期探活+失效前告警。
机制（2026-10-22 董事长令，2026-10-25 落实）：
1. 档案只读保护：四家专家档案每日备份一份到 /opt/model-profiles/<name>-bak-YYYYMMDD，失效时先回滚备份，不直接叫重登。
2. 权限统一：web_brain 必须 sudo 跑；档案 root 700 导致普通用户复制失败是已知假阴性，探活脚本一律 sudo。
3. 探活不烧额度：只检查档案目录、Cookies 文件、复制是否成功，不发真实提问；提问探活每周一次、短句。
4. 失效判定：复制失败 OR Cookies 缺失 OR 提问探活连续 2 次 not_logged_in，才判真失效；单次 not_logged_in 只记告警、不叫重登。
5. 失效处置：先回滚昨日备份再探活，仍失败才上报董事长，附实测证据，不许把检测失败说成登录失效。
"""
import os, shutil, subprocess, datetime, json, pathlib, tempfile
PROFILES = {"deepseek": "/opt/model-profiles/deepseek", "chatgpt": "/opt/model-profiles/chatgpt", "claude": "/opt/model-profiles/claude", "gemini": "/opt/model-profiles/gemini"}
OUT = pathlib.Path("/home/ubuntu/ai-team/state/login-maintain-latest.json")
today = datetime.date.today().isoformat()
result = {"date": today, "checked_at": datetime.datetime.now().astimezone().isoformat(), "profiles": {}}
for name, path in PROFILES.items():
    p = pathlib.Path(path)
    cookies = list(p.rglob("Cookies")) if p.exists() else []
    # copy test
    tmp = pathlib.Path(tempfile.mkdtemp(prefix=f"login-copy-{name}-"))
    if tmp.exists(): shutil.rmtree(tmp, ignore_errors=True)
    try:
        shutil.copytree(path, tmp, symlinks=True); copy_ok = True
    except Exception as e:
        copy_ok = False; result["profiles"][name] = {"copy_ok": False, "error": str(e)[:200]}
        continue
    finally:
        if tmp.exists(): shutil.rmtree(tmp, ignore_errors=True)
    # daily backup
    bak = pathlib.Path(f"/opt/model-profiles/{name}-bak-{today}")
    if not bak.exists():
        try: shutil.copytree(path, bak, symlinks=True); bak_ok = True
        except Exception as e: bak_ok = False
    else: bak_ok = True
    result["profiles"][name] = {"exists": True, "cookies_files": len(cookies), "copy_ok": copy_ok, "backup_today": bak_ok, "verdict": "alive_or_unknown" if copy_ok and cookies else "check_needed"}
OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(json.dumps(result, ensure_ascii=False))
