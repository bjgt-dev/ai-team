#!/usr/bin/env python3
import json
import os
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = os.getenv('AI_TEAM', '/home/ubuntu/ai-team')
M5_DIR = os.path.join(BASE, 'state', 'm5-20261105')

def load_store(name):
    path = os.path.join(M5_DIR, f'{name}.json')
    if not os.path.exists(path):
        return []
    with open(path, 'r') as file:
        return json.load(file)

def save_store(name, records):
    path = os.path.join(M5_DIR, f'{name}.json')
    with open(path, 'w') as file:
        json.dump(records, file)

def validate_cost(rec):
    errors = []
    if 'task_id' not in rec or 'model' not in rec or 'cost' not in rec or 'cost_status' not in rec or 'token_in' not in rec or 'token_out' not in rec or 'resource_cpu_hours' not in rec or 'resource_mem_gb_hours' not in rec or 'node' not in rec or 'infra_cost_known' not in rec or 'bill_snapshot' not in rec or 'recorded_at' not in rec:
        errors.append('Missing required fields')
    if rec['cost'] == 0 and rec['cost_status'] == 'unknown':
        errors.append('Cost cannot be zero with unknown status')
    return errors

def validate_default_action(rec):
    errors = []
    if 'task_id' not in rec or 'current_state' not in rec or 'default_next_action' not in rec or 'owner' not in rec or 'trigger_condition' not in rec or 'last_updated_at' not in rec or 'executed' not in rec or 'executed_at' not in rec or 'executed_by' not in rec:
        errors.append('Missing required fields')
    return errors

def validate_degrade_event(rec):
    errors = []
    if 'incident_id' not in rec or 'task_id' not in rec or 'model' not in rec or 'original_state' not in rec or 'degraded_state' not in rec or 'degrade_reason' not in rec or 'degraded_at' not in rec or 'degrade_action' not in rec or 'recover_condition' not in rec or 'recovered_at' not in rec or 'verify_result' not in rec or 'verified_by' not in rec:
        errors.append('Missing required fields')
    return errors

def add_record(store_name, rec):
    errors = []
    if store_name == 'cost':
        errors.extend(validate_cost(rec))
    elif store_name == 'default-action':
        errors.extend(validate_default_action(rec))
    elif store_name == 'degrade-event':
        errors.extend(validate_degrade_event(rec))
    else:
        errors.append('Invalid store name')
    if errors:
        return errors
    store = load_store(store_name)
    store.append(rec)
    save_store(store_name, store)
    return errors

CST = timezone(timedelta(hours=8))


def parse_ts(timestamp):
    # Naive store timestamps are CST wall time (store convention); aware
    # ones keep their offset. Unparseable values ('未恢复') -> None, never
    # raise: callers treat None as "no timestamp". (2026-11-06 fix: the old
    # fromisoformat-only version left naive values naive, so every
    # records_in_window comparison against the check's aware bounds raised
    # TypeError and the store path silently returned nothing.)
    if timestamp is None:
        return None
    try:
        dt = datetime.fromisoformat(str(timestamp).replace('Z', '+00:00'))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST)
    return dt.replace(microsecond=0)

def records_in_window(records, start, end, time_field):
    start = parse_ts(start)
    end = parse_ts(end)
    return [rec for rec in records if parse_ts(rec[time_field]) >= start and parse_ts(rec[time_field]) <= end]

def summarize_cost(records):
    count = len(records)
    known_count = sum(1 for rec in records if rec['infra_cost_known'])
    zero_free_count = sum(1 for rec in records if rec['cost'] == 0 and rec['cost_status'] == 'zero_free')
    unknown_count = sum(1 for rec in records if rec['cost'] == 0 and rec['cost_status'] == 'unknown')
    total_cpu_hours = sum(float(rec['resource_cpu_hours']) for rec in records if rec['resource_cpu_hours'] != 'unknown')
    total_mem_gb_hours = sum(float(rec['resource_mem_gb_hours']) for rec in records if rec['resource_mem_gb_hours'] != 'unknown')
    bill_snapshots = sum(1 for rec in records if rec['bill_snapshot'] != '缺数据' and Path(rec['bill_snapshot']).exists())
    return {
        'count': count,
        'known_count': known_count,
        'zero_free_count': zero_free_count,
        'unknown_count': unknown_count,
        'total_cpu_hours': total_cpu_hours,
        'total_mem_gb_hours': total_mem_gb_hours,
        'bill_snapshots': bill_snapshots
    }

def default_action_rate(records):
    total_triggered = sum(1 for rec in records if rec['trigger_condition'] != '视情况而定')
    executed_count = sum(1 for rec in records if rec['executed'])
    if total_triggered == 0:
        return {'total_triggered': total_triggered, 'executed_count': executed_count, 'rate': None, 'note': '无法计算'}
    return {
        'total_triggered': total_triggered,
        'executed_count': executed_count,
        'rate': executed_count / total_triggered
    }

def self_healed_count(records, start, end):
    # No window given -> count all records. recovered_at '未恢复' parses
    # to None and is never counted; only verify_result == '通过' counts.
    start_dt = parse_ts(start) if start else None
    end_dt = parse_ts(end) if end else None
    n = 0
    for rec in records:
        if rec.get('verify_result') != '通过':
            continue
        rt = parse_ts(rec.get('recovered_at'))
        if rt is None:
            continue
        if start_dt is not None and rt < start_dt:
            continue
        if end_dt is not None and rt > end_dt:
            continue
        n += 1
    return n

def main():
    parser = argparse.ArgumentParser(description='M5 field store management')
    parser.add_argument('--store', choices=['cost', 'default-action', 'degrade-event'], required=True, help='Store to operate on')
    parser.add_argument('--list', action='store_true', help='List records')
    parser.add_argument('--start', help='Start time for records')
    parser.add_argument('--end', help='End time for records')
    parser.add_argument('--validate', help='Validate JSON file')
    parser.add_argument('--summary', action='store_true', help='Summarize records')
    args = parser.parse_args()

    if args.validate:
        try:
            with open(args.validate, 'r') as file:
                records = json.load(file)
            if args.store == 'cost':
                errors = validate_cost(records)
            elif args.store == 'default-action':
                errors = validate_default_action(records)
            elif args.store == 'degrade-event':
                errors = validate_degrade_event(records)
            if errors:
                print(json.dumps(errors))
                exit(2)
            else:
                print('Valid records')
                exit(0)
        except Exception as e:
            print(json.dumps([str(e)]))
            exit(2)

    if args.list:
        records = load_store(args.store)
        if args.start and args.end:
            records = records_in_window(records, args.start, args.end, 'recorded_at' if args.store == 'cost' else 'last_updated_at' if args.store == 'default-action' else 'degraded_at')
        print(json.dumps(records))

    if args.summary:
        records = load_store(args.store)
        if args.start and args.end:
            records = records_in_window(records, args.start, args.end, 'recorded_at' if args.store == 'cost' else 'last_updated_at' if args.store == 'default-action' else 'degraded_at')
        if args.store == 'cost':
            print(json.dumps(summarize_cost(records)))
        elif args.store == 'default-action':
            print(json.dumps(default_action_rate(records)))
        elif args.store == 'degrade-event':
            print(json.dumps({'self_healed_count': self_healed_count(records, args.start, args.end)}))

if __name__ == '__main__':
    main()