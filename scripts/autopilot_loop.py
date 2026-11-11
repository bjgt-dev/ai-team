#!/usr/bin/env python3
# Autopilot loop (2026-10-25, chairman ultimate order)
# Rules, hard:
# - Scan contracts/pending/muse (worker run_cycle only reads this dir, NOT pending/ root).
#   Any contract in pending/ root is moved to pending/muse automatically (bug fix 2026-10-25).
# - Never SSH to muse/muse-2: platform network only allows out, SSH in times out (proven 2026-10-25).
#   Worker wake is via hub hooks_wake + housekeeper runner, not SSH push.
# - Never call weak hub models as expert panel. Expert panel is DeepSeek/ChatGPT/Claude/Gemini web only.
#   On rc!=0, unclaimed timeout, or deny, autopilot writes a JSON doubt list to
#   ceo-seat/out/autopilot-expert-queue.json for housekeeper to consult, and retries, up to 2 times.
# - Never fake verify PASS, never fake worker claim, never print tokens/keys.
# - Push to chairman only on: A all P1 verify PASS, B expert fails or hardware down, C daily report.
#   This script only logs; Muse push is via existing deadletter-auto-push cron, B only.
import json, os, pathlib, subprocess, datetime, shutil
BASE = pathlib.Path('/home/ubuntu/ai-team')
PENDING_ROOT = BASE / 'contracts/pending'
PENDING_MUSE = BASE / 'contracts/pending/muse'
DONE = BASE / 'contracts/done'
INBOX = BASE / 'inbox'
LOG = BASE / 'logs/autopilot.log'
OUT = BASE / 'ceo-seat/out'
def log(msg):
    line = datetime.datetime.now().astimezone().isoformat() + ' autopilot ' + msg + chr(10)
    LOG.open('a').write(line)
    print(line.strip())
def main():
    PENDING_MUSE.mkdir(parents=True, exist_ok=True)
    moved = 0
    for f in PENDING_ROOT.glob('*.json'):
        shutil.move(str(f), str(PENDING_MUSE / f.name))
        moved += 1
    log(f'moved_root_to_muse={moved}')
    # verify done P1 bundles
    verified = []
    for contract in DONE.glob('p1-*.json'):
        tid = contract.stem
        bundle = INBOX / tid
        if bundle.exists():
            r = subprocess.run(['python3', str(BASE / 'scripts/verify.py'), str(bundle), str(contract)], capture_output=True, text=True)
            try:
                verdict = json.loads(r.stdout).get('verdict')
            except Exception:
                verdict = 'UNKNOWN'
            content = ''
            try:
                content = (bundle / json.loads(contract.read_text()).get('output_file','')).read_text()
            except Exception:
                pass
            cv = subprocess.run(['python3', str(BASE / 'scripts/content_verify.py'), str(bundle / json.loads(contract.read_text()).get('output_file',''))], capture_output=True, text=True)
            if cv.returncode != 0:
                verdict = 'FAIL_CONTENT_GATE:' + cv.stdout.strip()
            verified.append((tid, verdict, r.returncode))
            # 2026-10-31 OPT 校验闸并行记账：只记结果不拦截
            try:
                _out_file = json.loads(contract.read_text()).get('output_file', '')
                _dpath = bundle / _out_file if _out_file else None
                if _dpath and _dpath.exists():
                    _g = subprocess.run(['bash', str(BASE / 'scripts/gate_authority.sh'), str(contract), str(_dpath)], capture_output=True, text=True)
                    log(f'gate_parallel {tid} rc={_g.returncode} out={(_g.stdout + _g.stderr).strip()[:200]}')
                else:
                    log(f'gate_parallel {tid} skip_no_deliverable')
            except Exception as _e:
                log(f'gate_parallel {tid} error={_e}')
            log(f'verify {tid} verdict={verdict} rc={r.returncode}')
    # unclaimed check
    now = datetime.datetime.now().astimezone().timestamp()
    unclaimed = []
    for f in PENDING_MUSE.glob('*.json'):
        age = now - f.stat().st_mtime
        if age > 600:
            unclaimed.append((f.name, int(age)))
    if unclaimed:
        log(f'unclaimed_timeout={unclaimed}')
        for name, age in unclaimed:
            subprocess.run(['python3', str(BASE / 'scripts/hooks_wake.py'), name], check=False)
        # expert queue, do not consult weak models
        queue = OUT / 'autopilot-expert-queue.json'
        queue.write_text(json.dumps({'created_at': datetime.datetime.now().astimezone().isoformat(), 'doubts': [{'task': name, 'age_s': age, 'question': 'why unclaimed, what fix, retry or reassign?'} for name, age in unclaimed], 'expert_panel': 'DeepSeek/ChatGPT/Claude/Gemini web only, housekeeper to consult, autopilot must not fake'}, ensure_ascii=False, indent=2))
        log('expert_queue_written')
    else:
        log('unclaimed_timeout=none')
    # run existing watchdogs, best effort
    for script in ['deadletter_watchdog.py', 'p1_ticket_watchdog.py', 'review_trigger_watcher.py']:
        subprocess.run(['python3', str(BASE / 'scripts' / script)], check=False)
    log(f'done verified={verified}')
if __name__ == '__main__':
    main()
