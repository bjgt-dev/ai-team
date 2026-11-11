import pathlib, datetime, subprocess, os
base = pathlib.Path('/home/ubuntu/ai-team')
tasks = sorted((base / 'ceo-seat/tasks').glob('p1-*.md'))
out = base / 'ceo-seat/out'
now = datetime.datetime.now().astimezone().isoformat()
claim = out / 'p1-claim-20261025.md'
for t in tasks:
    stem = t.stem
    ack = list(out.glob('*' + stem + '*')) + list(out.glob('*p1*'))
    # simple: check if any out file mentions stem
    found = False
    for f in [x for x in out.glob('*.md') if x.name not in ('event-bus-latest.md',)]:
        try:
            if stem in f.read_text(errors='ignore'):
                found = True
                break
        except Exception:
            pass
    if claim.exists():
        print('claimed', stem); continue
    if not found:
        log = base / 'logs/p1-ticket-watchdog.log'
        log.open('a').write(now + ' P1 no-ack ' + stem + chr(10))
        subprocess.run(['python3', str(base / 'scripts/hooks_wake.py'), stem], check=False)
        print('no-ack', stem)
    else:
        print('ack-found', stem)
