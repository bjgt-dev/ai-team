#!/usr/bin/env python3
"""精简入参：从 259KB 架构札记 + 59KB 决策队列里，只抽最近 10 条事实，生成 <2KB 摘要，供大管家每轮只读摘要，不读全文。"""
import json, pathlib, re
base = pathlib.Path("/home/ubuntu/ai-team")
try:
    # 2026-11-11 SLIMFIX (inc-20261110-slim-section-stale-md-date 选项 A，董事长 16:53 授权本席拍板):
    # 决策队列正典在 ceo-seat 侧，全局 state/decision-queue.json 长期 pending=[]。
    # 改读 ceo-seat 队列；读失败才兜底读全局。条目字段兼容两边（ts/id 与 created_at/task_id）。
    _qpath = base/"ceo-seat/state/decision-queue.json"
    try:
        q = json.load(open(_qpath))
    except Exception:
        q = json.load(open(base/"state/decision-queue.json"))
    pending = q.get("pending", [])
    summary = f"决策队列 pending={len(pending)}，最老={pending[0].get('ts', pending[0].get('created_at','?')) if pending else '-'}，最新3条={[p.get('id') or p.get('task_id') for p in pending[-3:]]}\n"
except Exception as e:
    summary = f"决策队列读失败 {e}\n"
try:
    arch = (base/"ceo-seat/out/butler-arch-note.md").read_text(encoding="utf-8", errors="ignore")
    # 2026-11-06 SLIMPLACE: 上游会话曾把整份札记覆写为单行占位文本，使摘要管道失效。
    # 只在“整份文件很短且命中占位句”时判定为被覆盖，避免误伤正文里对占位事件的正常记录；
    # 命中后回退到最近一份不含占位句的 .bak-* 备份（只读回退，不改动原文件）。
    _ph = ("全文、决策队列全文未找到", "无细节可提供")

    def _is_clobbered(_t):
        # 2026-11-06 PROMPTCLOB: 上游会话还可能把当班任务原文整份写进札记（单行、数百字）。
        # 旧 SLIMPLACE 只认"未找到"占位句，识别不到这种任务原文覆写，摘要会把任务原文当札记展示。
        # 判定条件收紧为：文件很短（<1200 字、换行 <3）且同时含任务开头与"只读精简摘要"。
        _s = _t.strip()
        return (len(_s) < 1200 and _s.count("\n") < 3) and ("常驻架构师当班" in _s) and ("只读精简摘要" in _s)

    if _is_clobbered(arch) or (len(arch.strip()) < 300 and any(_m in arch for _m in _ph)):
        _baks = sorted((base/"ceo-seat/out").glob("butler-arch-note.md.bak-*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for _b in _baks:
            try:
                _t = _b.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if not _is_clobbered(_t) and not (len(_t.strip()) < 300 and any(_m in _t for _m in _ph)):
                arch = _t
                summary += f"[提示] 札记命中占位/任务原文覆写，已回退到备份 {_b.name}\n"
                break
    lines = arch.strip().splitlines()
    # 2026-11-03 ARCHFIX: 札记既有"新条目置顶"也有"追加文末"两种写法，原 facts[-10:]
    # 只截到最末一节的尾部且会漏掉新置顶条目。改为按节切分、按节内最新实质时间戳选最新一节。
    sections, cur = [], []
    for ln in lines:
        if (ln.startswith("# ") or ln.startswith("## ")) and cur:
            sections.append(cur)
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        sections.append(cur)

    def _head_ts(sec):
        # 2026-11-07 HEADTS: 节标题自带“日期+时间”时以其为准，避免节内跨日时间
        # （如 00:15 节里的 23:56:59、16:18）被继承为当天时间而反超新节。
        head = sec[0]
        dm = re.search(r"(\d{4}-\d{2}-\d{2})", head)
        if not dm:
            return ""
        tm = re.search(r"(\d{2}:\d{2}(?::\d{2})?)", head)
        if not tm:
            return ""
        return dm.group(1) + "T" + tm.group(1)

    def _sec_ts(sec):
        head_ts = _head_ts(sec)
        if head_ts and any(ln.startswith("- ") and "八层审查：" not in ln for ln in sec):
            return head_ts
        m = re.search(r"(\d{4}-\d{2}-\d{2})", sec[0])
        day = m.group(1) if m else ""
        best = ""
        for ln in sec:
            if ln.startswith("#") or not ln.startswith("- "):
                continue
            if "八层审查：" in ln:
                continue
            # 2026-11-05 SECTS: 同一行里先出现完整日期、后跟纯时间时，纯时间必须继承
            # 该行最近的日期，不能回落到节标题日期。否则旧节里的"brain-alerts.log 停在
            # 11:11:34"（前文日期为 2026-11-04）会被误记为当天 11:11:34，压过新节真实
            # 时间，导致摘要长期选到旧节。
            # 2026-11-06 SLIMH2: 只从 "- " 事实行解析 ts，跳过标题行与空行。
            # 原逻辑对 "# " 轮次标题行（如 "# 架构札记（当班：2026-11-06 轮次 05:00）"）
            # 也解析 ts，导致只有标题+空行的空节被选为"最新节"，摘要正文为空。
            line_date = None
            # 2026-11-11 SLIMFIX (inc-20261110-slim-section-stale-md-date 选项 A):
            # 正则显式识别 M-D 简写（如“11-09 21:20:02”）；M-D 补节标题年份，
            # 仅当与节标题同月日时采用，否则按旧逻辑跳过（d != day）。
            for t in re.finditer(r"(?:(\d{4}-\d{2}-\d{2}|\d{2}-\d{2})[T ])?(\d{2}:\d{2}(?::\d{2})?)", ln):
                g1 = t.group(1)
                if g1:
                    if len(g1) == 5:
                        if not day:
                            continue
                        g1 = day[:5] + g1
                    line_date = g1
                d = line_date or day
                if not d or (day and d != day):
                    continue
                val = d + "T" + t.group(2)
                if val > best:
                    best = val
        return best

    # 2026-11-06 SLIMH1: 并列时取最后一节（最新追加的札记节），而非第一节（标题节）
    max_ts = max(_sec_ts(s) for s in sections) if sections else ""
    newest = [s for s in sections if _sec_ts(s) == max_ts][-1] if sections else []
    facts = [ln for ln in newest if not (ln.startswith("- ") and "八层审查：" in ln) and not ln.startswith("## ")]
    last = facts[:30]
    summary += "架构札记最新一节（按时间戳选）：\n" + "\n".join(last) + "\n"
except Exception as e:
    summary += f"架构札记读失败 {e}\n"

try:
    import datetime
    _tasks = sorted((base/"ceo-seat/tasks").glob("review-*.md"), key=lambda q: q.stat().st_mtime)
    _pend = []
    for _t in _tasks:
        _task = _t.stem[len("review-"):] if _t.stem.startswith("review-") else _t.stem
        # 2026-11-11 SLIMFIX (inc-20261110-slim-verdict-path-mismatch 选项 A):
        # verdict JSON 实际写在 ceo-seat/state/butler-verdicts/；先查 ceo-seat 再兜底查全局，
        # 任一存在即视为已评审。
        if not ((base/"ceo-seat/state/butler-verdicts"/(_task + ".json")).exists() or (base/"state/butler-verdicts"/(_task + ".json")).exists()):
            _age = int((datetime.datetime.now() - datetime.datetime.fromtimestamp(_t.stat().st_mtime)).total_seconds() // 60)
            _pend.append(f"- 待评审请求：{_task}（{_age} 分钟前，{_t}；结论写 ceo-seat/state/butler-verdicts/{_task}.json，verdict 为 通过/有条件通过/打回）")
    if _pend:
        summary += "待大管家评审的请求（无结论，先审再派发）：\n" + "\n".join(_pend) + "\n"
except Exception as e:
    summary += f"评审请求扫描失败 {e}\n"
out = base/"ceo-seat/out/butler-slim-summary.md"
out.write_text(summary, encoding="utf-8")
print(f"slim bytes={len(summary.encode())} -> {out}")
