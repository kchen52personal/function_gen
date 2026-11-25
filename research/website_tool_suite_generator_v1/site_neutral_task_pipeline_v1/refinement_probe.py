#!/usr/bin/env python3
"""Site-neutral, development-only probe of improved format instructions."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import development
from evaluate import evaluate_case

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def save(path, value):
    temp = path.with_name('.' + path.name + '.' + uuid4().hex + '.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    os.replace(temp, path)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source_run', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--first-only', action='store_true', help='run one bounded development task')
    args = parser.parse_args()
    source = args.source_run.resolve()
    summary = json.loads((source/'summary.json').read_text())
    manifest = source/'tasks/development/tasks.json'
    if digest(manifest) != summary['split_hashes']['development']:
        raise ValueError('development manifest hash mismatch')
    dev = json.loads(manifest.read_text())['tasks']
    if len(dev) != 8:
        raise ValueError('expected eight development tasks')
    tasks = [dev[0], dev[4]]  # Same two as the previous paired comparison.
    if args.first_only:
        tasks = tasks[:1]
    contract = json.loads((source/'tasks/contract.json').read_text())
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    for name in ('.config','.cache','.tmp'):
        (out/name).mkdir()
    os.environ.update(XDG_CONFIG_HOME=str(out/'.config'), XDG_CACHE_HOME=str(out/'.cache'),
                      TMPDIR=str(out/'.tmp'), PLAYWRIGHT_BROWSERS_PATH=str(development.PROJECT/'.playwright-browsers'))
    state = {'status':'running', 'source_run':str(source), 'development_sha256':digest(manifest),
             'started_at':datetime.now(timezone.utc).isoformat(), 'model':'qwen3.8-flash',
             'task_ids':[x['task_id'] for x in tasks], 'held_out_opened':False, 'attempts':[],
             'limits':{'per_attempt_seconds':900, 'agent_calls':50, 'inner_calls':12, 'reference_calls':12},
             'limitations':['Development-only exploratory reruns; not an independent test.',
                 'Live page, API latency and agent outputs may change between runs.',
                 'Automated agreement is not a guarantee of correctness.',
                 'Static safety checks do not enforce read-only website access.']}
    checkpoint = lambda: save(out/'summary.json',state)
    checkpoint()
    try:
        for task in tasks:
            state['current'] = task['task_id']
            checkpoint()
            agent_root = out/'agents'/task['task_id']
            agent_root.parent.mkdir(parents=True, exist_ok=True)
            development.run(summary['website'], contract, [task], agent_root, model='qwen3.8-flash',
                            attempt_timeout=900, queue_timeout=940)
            records = json.loads((agent_root/'summary.json').read_text()).get('attempts', [])
            record = next((r for r in records if r.get('task_id') == task['task_id']), {})
            script = agent_root/task['task_id']/'qwen3.8-flash/agent/final_script.py'
            if (record.get('trajectory_status') == 'Submitted' and record.get('process_exit') == 0
                    and script.is_file() and not script.is_symlink() and digest(script) == record.get('script_sha256')):
                assessment = evaluate_case(summary['website'], contract, task, script, out/'assessment'/task['task_id'])
            else:
                assessment = {'task_id':task['task_id'], 'status':'not_assessable', 'reason':'no submitted hash-matched script'}
            state['attempts'].append({'task_id':task['task_id'], 'trajectory_status':record.get('trajectory_status'),
                'process_exit':record.get('process_exit'), 'agent_calls':record.get('agent_calls'),
                'inner_tool_calls':record.get('inner_tool_calls'), 'assessment':assessment})
            checkpoint()
        state.pop('current',None)
        state.update(status='complete_exploratory', finished_at=datetime.now(timezone.utc).isoformat())
        checkpoint()
    except Exception as exc:
        state.update(status='stopped_error', error_type=type(exc).__name__, error_message=str(exc)[:500])
        checkpoint()
        raise

if __name__ == '__main__':
    main()
