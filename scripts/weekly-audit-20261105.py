#!/usr/bin/env python3
"""
Read-only quality audit script for weekly checks.
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))
BASE = os.environ.get('AI_TEAM', '/home/ubuntu/ai-team')

def parse_ts(s):
    try:
        dt = datetime.strptime(s, '%Y-%m-%d %H:%M[:%S]')
        return dt.replace(tzinfo=CST)
    except ValueError:
        try:
            dt = datetime.fromisoformat(s)
            return dt.replace(tzinfo=CST)
        except ValueError:
            return None

def parse_dsn():
    with open('/home/ubuntu/ai-team/state/.cq-dsn', 'r') as f:
        dsn = f.readline().strip()
    dsn = dsn[5:].strip('"')
    pattern = re.compile(r'(host|port|dbname|user|password)=([^\s]+)')
    result = {}
    for key, value in pattern.findall(dsn):
        result[key] = value
    return result

def psql_rows(sql):
    dsn = parse_dsn()
    host = dsn['host']
    port = dsn['port']
    dbname = dsn['dbname']
    user = dsn['user']
    password = dsn['password']
    env = os.environ.copy()
    env['PGPASSWORD'] = password
    try:
        result = subprocess.run(
            ['psql', '-h', host, '-p', port, '-d', dbname, '-U', user, '-v', 'ON_ERROR_STOP=1', '-At', '-F', '|', '-c', sql],
            env=env,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        return [line.strip() for line in result.stdout.decode().splitlines() if line.strip()]
    except subprocess.CalledProcessError as e:
        raise RuntimeError(e.stderr.decode()[:300])

def load_json(path):
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def audit_items_1_5(start, end):
    sql = f"""
    SELECT status, retry_count, payload::text FROM central_tasks
    WHERE created_at >= '{start}' AND created_at < '{end}'
    """
    rows = psql_rows(sql)
    result = {
        'item1': {'total': 0, 'by_status': {}, 'source': 'central_tasks'},
        'item2': {'done': 0, 'terminated': 0, 'rate': None, 'source': 'central_tasks'},
        'item3': {'submitted': 0, 'first_pass': 0, 'rework': 0, 'rate': None, 'source': 'central_tasks', 'note': ''},
        'item4': {'submitted': 0, 'rework': 0, 'rate': None, 'dist': {'1': 0, '2': 0, '3+_dead': 0}, 'source': 'central_tasks'},
        'item5': {'executed': 0, 'retried': 0, 'rate': None, 'by_class': {}, 'source': 'central_tasks'}
    }
    for row in rows:
        status, retry_count, payload = row.split('|', 2)
        payload = json.loads(payload)
        result['item1']['total'] += 1
        result['item1']['by_status'][status] = result['item1']['by_status'].get(status, 0) + 1
        if status.upper() == 'DONE':
            result['item2']['done'] += 1
        elif status.upper() in ('DEAD', 'TERMINAL_FAILED'):
            result['item2']['terminated'] += 1
        if status.upper() in ('DONE', 'RETRY_WAIT', 'QUARANTINED'):
            result['item3']['submitted'] += 1
            result['item4']['submitted'] += 1
        if 'rework' in payload:
            result['item3']['rework'] += 1
            result['item4']['rework'] += 1
            _rc = payload.get('rework_count', 1)
            try:
                _rc = int(_rc)
            except Exception:
                _rc = 1
            _key = '1' if _rc <= 1 else ('2' if _rc == 2 else '3+_dead')
            result['item4']['dist'][_key] += 1
        if 'failure_class' in payload:
            failure_class = payload['failure_class']
            result['item5']['by_class'][failure_class] = result['item5']['by_class'].get(failure_class, 0) + 1
            result['item5']['executed'] += 1
        if int(retry_count or 0) > 0:
            result['item5']['retried'] += 1
    _t2 = result['item2']['done'] + result['item2']['terminated']
    result['item2']['terminated_total'] = _t2
    result['item2']['rate'] = (result['item2']['done'] / _t2) if _t2 else None  # terminated_total = done + failed-terminal; rate = done / terminated_total
    _s3 = result['item3']['submitted']
    result['item3']['first_pass'] = _s3 - result['item3']['rework']
    result['item3']['rate'] = (result['item3']['first_pass'] / _s3) if _s3 else None
    if not _s3:
        result['item3']['note'] = '缺数据：窗口内无送核验任务'
    _s4 = result['item4']['submitted']
    result['item4']['rate'] = (result['item4']['rework'] / _s4) if _s4 else None
    _e5 = result['item1']['total']  # executed = all tasks in window
    result['item5']['executed'] = _e5
    result['item5']['rate'] = (result['item5']['retried'] / _e5) if _e5 else None
    return result


def audit_items_6_10(start, end):
    # item6: tool success rate from the canary jsonl (real JSON parsing).
    item6 = {'rate': None, 'per_tool': {}, 'source': '缺数据', 'note': ''}
    try:
        total = 0
        ok = 0
        cj = os.path.join(BASE, 'state', 'gateway-canary-20261104.jsonl')
        with open(cj, encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                ts = parse_ts(d.get('ts'))
                if ts is None or not (start <= ts <= end):
                    continue
                total += 1
                if d.get('pass3'):
                    ok += 1
        if total == 0:
            item6['note'] = '缺数据：窗口内无金丝雀记录'
        else:
            item6['rate'] = ok / total
            item6['per_tool'] = {'gateway-canary': {'ok': ok, 'total': total}}
            item6['source'] = 'state/gateway-canary-20261104.jsonl'
    except OSError:
        item6['note'] = '缺数据：金丝雀日志不可读'
    out = {'item6': item6}
    out['item7'] = audit_items_7(start, end)
    out['item8'] = audit_items_8(start, end)
    out['item9'] = audit_items_9(start, end)
    return out


def audit_items_7(start, end):
    fake_list, uncheckable = [], 0
    checked = 0
    try:
        rows = psql_rows("SELECT task_id, payload::text FROM central_tasks WHERE status='DONE' ORDER BY updated_at DESC LIMIT 20")
        for row in rows:
            parts = row.split('|', 1)
            if len(parts) != 2:
                uncheckable += 1
                continue
            task_id, payload_text = parts
            try:
                payload = json.loads(payload_text)
            except ValueError:
                uncheckable += 1
                continue
            output_path = payload.get('output_path') or payload.get('deliverable')
            if not output_path:
                # Fall back: contract file (state/l3-20261105-contracts/*.json)
                # indexed by task_id, or the llm_doc deliverable path.
                contract_ref = payload.get('contract')
                if isinstance(contract_ref, dict):
                    output_path = contract_ref.get('output_path')
                elif isinstance(contract_ref, str):
                    for _cf in glob.glob(os.path.join(BASE, 'state', 'l3-20261105-contracts', '*.json')):
                        try:
                            _cd = json.load(open(_cf, encoding='utf-8'))
                        except Exception:
                            continue
                        if _cd.get('task_id') == contract_ref or contract_ref in os.path.basename(_cf):
                            output_path = _cd.get('output_path')
                            break
                if not output_path:
                    _cand = os.path.join(BASE, 'state', 'deliverables', task_id + '.md')
                    if os.path.isfile(_cand):
                        output_path = _cand
            if not output_path:
                uncheckable += 1
                continue
            path = output_path if os.path.isabs(output_path) else os.path.join(BASE, output_path)
            try:
                with open(path, encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            except OSError:
                uncheckable += 1
                continue
            checked += 1
            is_fake = False
            if len(content) < 500 and content.startswith('pytest_exit=0'):
                is_fake = True
            contract_input = payload.get('contract_input')
            if contract_input and content.strip() == str(contract_input).strip():
                is_fake = True
            if is_fake:
                fake_list.append(task_id)
        note = '' if checked else '缺数据：无可核对交付物'
        return {'sampled': 20, 'checked': checked, 'fake_count': len(fake_list),
                'fake_share': (len(fake_list) / checked) if checked else None,
                'fake_list': fake_list, 'uncheckable': uncheckable,
                'method': 'latest 20 DONE', 'source': 'central_tasks', 'note': note}
    except Exception as e:
        return {'sampled': 0, 'checked': 0, 'fake_count': 0, 'fake_share': None,
                'fake_list': [], 'uncheckable': 0, 'method': 'latest 20 DONE',
                'source': 'central_tasks', 'note': '缺数据：%s' % str(e)[:100]}


def audit_items_8(start, end):
    by_class = {c: 0 for c in ('F-429', 'F-503', 'F-TIMEOUT', 'F-EMPTY', 'F-FORMAT',
                               'F-CONTRACT', 'F-VERIFY', 'F-ROUTING', 'F-UNDIAG')}
    total_failed = 0
    try:
        rows = psql_rows("SELECT payload::text FROM central_tasks WHERE status IN ('RETRY_WAIT', 'QUARANTINED') AND updated_at >= '%s' AND updated_at < '%s'" % (
            start.strftime('%Y-%m-%d %H:%M:%S'), end.strftime('%Y-%m-%d %H:%M:%S')))
        for row in rows:
            try:
                payload = json.loads(row)
            except ValueError:
                payload = {}
            cls = payload.get('failure_class', 'F-UNDIAG')
            if cls not in by_class:
                cls = 'F-UNDIAG'
            by_class[cls] += 1
            total_failed += 1
        no_sample = [c for c, n in by_class.items() if n == 0]
        return {'total_failed': total_failed, 'by_class': by_class,
                'no_sample': no_sample, 'source': 'central_tasks'}
    except Exception as e:
        return {'total_failed': 0, 'by_class': by_class,
                'no_sample': list(by_class), 'source': 'central_tasks',
                'note': '缺数据：%s' % str(e)[:100]}


def audit_items_9(start, end):
    nodes = {}
    claimer_present = False
    claimer_paused = False
    try:
        rows = psql_rows("SELECT payload::text FROM central_tasks WHERE updated_at >= '%s' AND updated_at < '%s'" % (
            start.strftime('%Y-%m-%d %H:%M:%S'), end.strftime('%Y-%m-%d %H:%M:%S')))
        for row in rows:
            try:
                payload = json.loads(row)
            except ValueError:
                continue
            node = payload.get('node') or payload.get('worker')
            if node:
                nodes.setdefault(node, {'claimed': 0, 'failed': 0})
                nodes[node]['claimed'] += 1
        try:
            crontab_output = subprocess.run(['crontab', '-l'], capture_output=True, text=True).stdout
        except Exception:
            crontab_output = ''
        for line in crontab_output.splitlines():
            if 'cq_claim' in line or 'claim' in line:
                claimer_present = True
                if 'PAUSED' in line:
                    claimer_paused = True
        return {'nodes': nodes if nodes else '缺数据',
                'claimer_present': claimer_present, 'claimer_paused': claimer_paused,
                'source': 'central_tasks and crontab'}
    except Exception as e:
        return {'nodes': '缺数据', 'claimer_present': False, 'claimer_paused': False,
                'source': 'central_tasks and crontab', 'note': '缺数据：%s' % str(e)[:100]}


def top3_problems(items):
    problems = []
    i7 = items.get('item7') or {}
    if i7.get('fake_count', 0) > 0:
        problems.append({'problem': '交付物存在虚假成功 %d 件' % i7['fake_count'],
                         'evidence_item': 'item7', 'owner': '大管家',
                         'suggestion': '逐件复核交付物内容，假 DONE 记入 reject-ledger 并返工',
                         'deadline': '立即'})
    i8 = items.get('item8') or {}
    if i8.get('total_failed', 0) > 0:
        top_cls = max((i8.get('by_class') or {}).items(), key=lambda kv: kv[1], default=(None, 0))
        problems.append({'problem': '失败任务 %d 件，最多一类 %s %d 件' % (i8['total_failed'], top_cls[0], top_cls[1]),
                         'evidence_item': 'item8', 'owner': '总调度',
                         'suggestion': '按失败类分派重试/换模型/上报',
                         'deadline': '1天内'})
    i9 = items.get('item9') or {}
    if i9.get('claimer_paused'):
        problems.append({'problem': 'claimer 定时器 PAUSED，无活跃认领方',
                         'evidence_item': 'item9', 'owner': '总调度',
                         'suggestion': '恢复 claimer 或由总调度兜底认领',
                         'deadline': '立即'})
    return problems[:3]


def fmt_rate(rate):
    if rate is None:
        return '缺数据'
    return '%.1f%%' % (rate * 100)


def write_report(items, start, end, out_dir, window_label):
    os.makedirs(out_dir, exist_ok=True)
    stamp = start.strftime('%Y%m%d-%H%M')
    md_path = os.path.join(out_dir, 'weekly-audit-%s.md' % stamp)
    json_path = os.path.join(out_dir, 'weekly-audit-%s.json' % stamp)
    missing = []
    lines = []
    lines.append('# 每周质量审计报告 %s' % (window_label or stamp))
    lines.append('')
    lines.append('窗口：%s 至 %s' % (start.strftime('%Y-%m-%d %H:%M:%S'), end.strftime('%Y-%m-%d %H:%M:%S')))
    lines.append('覆盖：central_tasks 库表（窗口内任务）+ state/gateway-canary-20261104.jsonl + crontab；未覆盖：rework 执行记录明细（以 payload 中 rework 标记为准）、工具调用全量日志。')
    lines.append('')
    for key in ('item1', 'item2', 'item3', 'item4', 'item5', 'item6', 'item7', 'item8', 'item9'):
        d = items.get(key) or {}
        lines.append('## %s' % key)
        lines.append('来源：%s' % d.get('source', '缺数据'))
        for k, v in d.items():
            if k in ('source', 'note'):
                continue
            lines.append('- %s: %s' % (k, v))
        if d.get('note'):
            lines.append('- 备注：%s' % d['note'])
            if '缺数据' in d['note'] or d.get('rate') is None and key in ('item2', 'item3', 'item4', 'item5', 'item6'):
                missing.append(key)
        lines.append('')
    lines.append('## 缺失数据清单')
    lines.append(', '.join(missing) if missing else '无（10 项均有数据）')
    lines.append('')
    lines.append('## Top 3 问题与修复建议')
    top3 = items.get('item10') or []
    if not top3:
        lines.append('无数据支撑的问题（不足 3 件不凑数）。')
    for i, p in enumerate(top3, start=1):
        lines.append('%d. %s（依据 %s；责任方 %s；建议 %s；期限 %s）' % (
            i, p.get('problem'), p.get('evidence_item'), p.get('owner'),
            p.get('suggestion'), p.get('deadline')))
    lines.append('')
    lines.append('审计只读，未改生产状态')
    lines.append('生产状态改动：未改动')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    return md_path, json_path


def main():
    parser = argparse.ArgumentParser(description='Weekly Audit (read-only)')
    parser.add_argument('--start', type=str,
                        default=(datetime.now(CST) - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S'))
    parser.add_argument('--end', type=str,
                        default=datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S'))
    parser.add_argument('--out-dir', type=str, default='/home/ubuntu/ai-team/state/outbox')
    parser.add_argument('--window-label', type=str, default='')
    args = parser.parse_args()
    start = parse_ts(args.start)
    end = parse_ts(args.end)
    if start is None or end is None:
        print('Error: Invalid date format')
        return 2
    try:
        items = audit_items_1_5(start, end)
        items.update(audit_items_6_10(start, end))
        items['item10'] = top3_problems(items)
        md_path, json_path = write_report(items, start, end, args.out_dir, args.window_label)
        print('audit done: %s' % md_path)
        return 0
    except RuntimeError as e:
        print('Error: %s' % e)
        return 2


if __name__ == '__main__':
    sys.exit(main())
