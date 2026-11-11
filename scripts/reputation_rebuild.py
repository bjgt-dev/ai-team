#!/usr/bin/env python3
# 工人信誉台账自动重建（2026-10-21 冷启动演练整改）：不改冻结文件，
# 从 manifest（归属）+ review_audit.jsonl（结局）周期重建，归属先到先存防证据包消失。
import glob, json, os, time

ROOT = '/home/ubuntu/ai-team'
STATE = os.path.join(ROOT, 'state/worker_reputation.json')
OUT = os.path.join(ROOT, 'state/worker_reputation.md')

def load():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}

def main():
    data = load()
    for pat in ('inbox/*/manifest.json', 'quarantine/*/bundle/manifest.json',
                'quarantine/*/manifest.json'):
        for f in glob.glob(os.path.join(ROOT, pat)):
            try:
                m = json.load(open(f))
            except Exception:
                continue
            tid, w = m.get('task_id'), m.get('worker_id')
            if tid and w and tid not in data:
                data[tid] = {'worker': w, 'outcome': None}
    audit = os.path.join(ROOT, 'logs/review_audit.jsonl')
    if os.path.exists(audit):
        for line in open(audit):
            try:
                r = json.loads(line)
            except Exception:
                continue
            tid = r.get('task_id')
            if not tid:
                continue
            v, rv = r.get('verify'), r.get('review')
            if v == 'PASS' and rv in ('PASS', 'none', None):
                oc = 'PASS'
            elif v == 'INCOMPLETE':
                oc = 'INCOMPLETE'
            else:
                oc = 'REJECT'
            data.setdefault(tid, {'worker': None, 'outcome': None})
            data[tid]['outcome'] = oc
    for f in glob.glob(os.path.join(ROOT, 'contracts/done/*.json')):
        tid = os.path.basename(f)[:-5]
        data.setdefault(tid, {'worker': None, 'outcome': None})
        if data[tid]['outcome'] is None:
            data[tid]['outcome'] = 'PASS'
    tmp = STATE + '.tmp'
    json.dump(data, open(tmp, 'w'), ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)
    agg = {}
    for tid, d in data.items():
        w = d.get('worker') or '未归属'
        a = agg.setdefault(w, {'PASS': 0, 'REJECT': 0, 'INCOMPLETE': 0})
        if d.get('outcome') in a:
            a[d['outcome']] += 1
    lines = ['# 工人信誉积分台账（自动重建，勿手改）',
             '口径：过件率 = PASS / (PASS+REJECT+INCOMPLETE)；归属源=证据包 manifest.worker_id，结局源=logs/review_audit.jsonl（同单取最新）。样本 <5 只记数不评级；>=5：>=0.8 可靠 / >=0.5 可用 / 其余 观察。',
             '重建时间：' + time.strftime('%Y-%m-%d %H:%M %z'),
             '',
             '| 工人 | PASS | REJECT | INCOMPLETE | 过件率 | 评级 |',
             '|---|---|---|---|---|---|']
    for w in sorted(agg):
        a = agg[w]
        tot = a['PASS'] + a['REJECT'] + a['INCOMPLETE']
        rate = ('%d/%d (%.0f%%)' % (a['PASS'], tot, 100.0 * a['PASS'] / tot)) if tot else '-'
        if tot < 5:
            grade = '未评级'
        else:
            r = a['PASS'] / tot
            grade = '可靠' if r >= 0.8 else ('可用' if r >= 0.5 else '观察')
        lines.append('| %s | %d | %d | %d | %s | %s |' % (w, a['PASS'], a['REJECT'], a['INCOMPLETE'], rate, grade))
    open(OUT, 'w').write('\n'.join(lines) + '\n')
    print('REPUTATION_OK tasks=%d' % len(data))

if __name__ == '__main__':
    main()
