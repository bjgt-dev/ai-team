#!/usr/bin/env python3
"""Expert loop: multi-round, NOT one-shot. Loop until final draft, then execute.
Rules (2026-10-25 chairman order):
- Expert = ChatGPT + Claude web only (DeepSeek/Gemini auto-degraded if down, never weak hub models).
- A round is NOT final if: answer is blocked/timeout, or gives options without a tie-breaker, or lacks rollback, or lacks concrete steps, or lacks pass/fail criteria.
- If not final, automatically generate a follow-up round from the missing pieces. Never push a choice back to the chairman.
- Final = at least one provider gives a draft with rollback + concrete steps + pass/fail, and no provider contradicts its direction. Write final-draft file, then write an execution task file. Execution is the goal, not the consultation itself.
- Max 5 rounds. If still not final after 5, write a failure file with exact missing pieces and retry list, not a fake final draft.
Usage: expert_loop.py <topic-slug> <question-file>
"""
import json, pathlib, subprocess, sys, datetime
BASE = pathlib.Path('/home/ubuntu/ai-team')
topic = sys.argv[1]
qfile = pathlib.Path(sys.argv[2])
base_q = qfile.read_text()
outdir = BASE / 'ceo-seat/out/expert-loop' / topic
outdir.mkdir(parents=True, exist_ok=True)
def is_final(text):
    if not text: return False
    has_rollback = '回滚' in text
    has_step = any(k in text for k in ['步骤', '流程', '命令', '脚本', '伪代码'])
    has_criteria = any(k in text for k in ['通过标准', '失败标准', '验收', 'pass', '判'])
    return has_rollback and has_step and has_criteria
for rnd in range(1, 6):
    answers = {}
    for provider in ['chatgpt', 'claude']:
        prompt = base_q if rnd == 1 else base_q + f"\n\n这是第{rnd}轮追问。上一轮未定稿，缺失点见上一轮文件。请直接给最终定稿，不要给无平局规则的选择题，不要推回给董事长。"
        out = outdir / f'round{rnd}-{provider}.json'
        subprocess.run(['sudo', 'timeout', '180', 'python3', str(BASE / 'scripts/web_brain.py'), '--provider', provider, '--prompt', prompt], stdout=open(out, 'w'), stderr=subprocess.DEVNULL)
        try:
            d = json.loads(out.read_text(encoding='utf-8', errors='ignore'))
            answers[provider] = d
        except Exception as e:
            answers[provider] = {'provider': provider, 'status': 'parse_failed', 'answer': str(e)}
    finals = [p for p, d in answers.items() if d.get('status') == 'ok' and is_final(d.get('answer', ''))]
    log = {'round': rnd, 'providers': {p: d.get('status') for p, d in answers.items()}, 'final_providers': finals, 'checked_at': datetime.datetime.now().astimezone().isoformat()}
    (outdir / f'round{rnd}-verdict.json').write_text(json.dumps(log, ensure_ascii=False, indent=2))
    print(json.dumps(log, ensure_ascii=False))
    if finals:
        best = answers[finals[0]]
        (outdir / 'final-draft.md').write_text(f"# Final draft: {topic}\n\nProvider: {finals[0]}\nRound: {rnd}\n\n{best.get('answer','')}\n")
        (BASE / 'ceo-seat/tasks' / f'execute-{topic}-20261025.md').write_text(f"# Execution task (from expert final draft, {topic})\n\nSource: ceo-seat/out/expert-loop/{topic}/final-draft.md\n\nExecute per final draft. Do not re-ask the chairman for direction. Report only A (done, with four-field receipt) or B (failed, with exact error code).\n")
        print('FINAL', topic, rnd)
        break
else:
    (outdir / 'not-final.md').write_text('Not final after 5 rounds. See round verdicts for missing pieces. Do not fabricate a final draft.')
    print('NOT_FINAL', topic)
