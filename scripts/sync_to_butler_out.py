import pathlib, shutil, datetime
BASE = pathlib.Path('/home/ubuntu/ai-team')
OUT = BASE / 'ceo-seat/out/sync'
OUT.mkdir(parents=True, exist_ok=True)
copied = []
for src in list((BASE / 'inbox').glob('p1-*/out/*.md')) + list((BASE / 'contracts/done').glob('p1-*.json')) + list((BASE / 'ceo-seat/out').glob('p1-*-r2-*.md')) + list((BASE / 'ceo-seat/out').glob('p1-01-expert-*.md')) + [BASE / 'ceo-seat/out/iptables-backup-20261025.txt', BASE / 'ceo-seat/out/autopilot-cron-20261025.txt']:
    if src.exists() and src.parent != OUT:
        dst = OUT / src.name
        shutil.copy2(src, dst)
        copied.append(src.name)
summary = OUT / 'shell-evidence-summary-20261025.md'
summary.write_text('# Shell evidence summary\n' + 'time: ' + datetime.datetime.now().astimezone().isoformat() + '\n' + '\n'.join(copied) + '\n')
print('copied', len(copied))
