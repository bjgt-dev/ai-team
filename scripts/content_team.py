#!/usr/bin/env python3
# 内容流水线 content_team.py（2026-10-20 董事长令：内容部三岗）
# 岗位1 撰稿人：由契约流水线产出并过 verify.py 门禁的事实稿（本脚本的输入）
# 岗位2 主编/去AI味编辑：人类化重写（禁词库+短句口语+父亲语气），铁律：不许新增事实
# 岗位3 视觉与排版：小红书版式（emoji 分段/多空行/标题/标签）+ 生图 Prompt
# 用法：content_team.py <初稿.md> <输出目录>；模型走 newapi（NEWAPI_BASE/NEWAPI_KEY 环境变量）
import json, os, sys, urllib.request
from pathlib import Path

SRC, OUT = Path(sys.argv[1]), Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
BASE = os.environ.get("NEWAPI_BASE", "http://127.0.0.1:3001/v1").rstrip("/")
KEY = os.environ["NEWAPI_KEY"]
MODEL = os.environ.get("CONTENT_MODEL", "foreman-prime")
BANNED = ["在这个时代", "综上所述", "不难发现", "这其实是", "值得一提的是",
          "众所周知", "随着科技的发展", "无疑", "总而言之", "由此可见"]

def chat(system, user, max_tokens=6000):
    body = json.dumps({"model": MODEL, "max_tokens": max_tokens,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": user}]}).encode()
    req = urllib.request.Request(BASE + "/chat/completions", data=body,
                                 headers={"Authorization": "Bearer " + KEY,
                                          "Content-Type": "application/json"})
    j = json.loads(urllib.request.urlopen(req, timeout=600).read())
    return j["choices"][0]["message"].get("content") or ""

draft = SRC.read_text()

EDITOR_SYS = (
    "你是一位资深主编，专长是把 AI 稿改成真人写的样子。作者人设：理工男爸爸，爱折腾代码，"
    "说话直、有点碎碎念，对孩子的事又特别上心。重写规则："
    "1) 禁用词绝对不许出现：在这个时代、综上所述、不难发现、这其实是、值得一提的是、众所周知、随着科技的发展、无疑、总而言之、由此可见；"
    "2) 多用短句和口语，长句拆开；允许语气词、允许小停顿和情绪起伏，像在家庭群里分享日常；"
    "3) 注入父亲视角的语气：可以自嘲、吐槽自己、流露小骄傲，但只能是爸爸的感受和评论；"
    "4) 真实性铁律：不许新增原文没有的事实——不许编孩子做过的事、不许编孩子说过的话、不许新增道具和场景；"
    "原文的事件、顺序、细节一个不许丢。只输出重写后的正文（含标题），不要解释。")
edited = chat(EDITOR_SYS, draft)
(OUT / "editor.md").write_text(edited if edited.endswith("\n") else edited + "\n")

VISUAL_SYS = (
    "你是小红书排版编辑。把主编稿排成小红书图文笔记正文："
    "第一行给一个口语化、带具体细节的标题（不要书名号体）；"
    "正文按场景分段，每段开头配一个贴切的 emoji，段与段之间空一行；"
    "保留全部事实与细节，不增删情节；结尾加一行互动提问，再加一行话题标签"
    "（从这些里选：#AI陪伴 #英语启蒙 #极客奶爸 #育儿日记 #人工智能教育 #亲子时光）；"
    "只输出排版后的正文。")
xhs = chat(VISUAL_SYS, edited)
(OUT / "xiaohongshu.md").write_text(xhs if xhs.endswith("\n") else xhs + "\n")

IMG_SYS = (
    "你是视觉策划。根据文章内容写 3 条生图 Prompt（给免费生图工具用），"
    "分别对应：封面图、文中场景图（孩子教机器人唱歌）、氛围图。"
    "每条 Prompt 给中文版一行 + 英文版一行，风格统一为温暖写实插画风、家庭客厅场景；"
    "画面里不要出现文字。不许出现原文没有的道具（笔记本、电脑、麦克风、乐器都不许加）；"
    "机器人外形不要臆测，只写圆润可爱的小机器人。只输出 3 组 Prompt。")
imgs = chat(IMG_SYS, edited, max_tokens=2000)
(OUT / "image-prompts.md").write_text(imgs if imgs.endswith("\n") else imgs + "\n")

hits = {w: edited.count(w) for w in BANNED if edited.count(w)}
print("PIPELINE_OK model:", MODEL)
print("editor banned-word hits:", hits if hits else "NONE")
print("outputs:", [p.name for p in sorted(OUT.iterdir())])
