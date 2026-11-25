#!/usr/bin/env python3
"""Repeat two timed-out development tasks with a longer wall-clock limit."""
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
    args = parser.parse_args()
    source = args.source_run.resolve()
    source_summary = json.loads((source/'summary.json').read_text())
    source_attempts = json.loads((source/'development_agents/summary.json').read_text())['attempts']
    selected_ids = ('task_008', 'task_002')
    if any(not any(x['task_id'] == tid and x['process_exit'] == 124 for x in source_attempts)
           for tid in selected_ids):
        raise ValueError('probe inputs must be prior timed-out tasks')
    manifest = source/'tasks/development/tasks.json'
    if digest(manifest) != source_summary['split_hashes']['development']:
        raise ValueError('development manifest hash mismatch')
    all_tasks = json.loads(manifest.read_text())['tasks']
    tasks = [next(x for x in all_tasks if x['task_id'] == tid) for tid in selected_ids]
    contract = json.loads((source/'tasks/contract.json').read_text())
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    for name in ('.config', '.cache', '.tmp'):
        (out/name).mkdir()
    os.environ.update(XDG_CONFIG_HOME=str(out/'.config'), XDG_CACHE_HOME=str(out/'.cache'),
                      TMPDIR=str(out/'.tmp'), PLAYWRIGHT_BROWSERS_PATH=str(development.PROJECT/'.playwright-browsers'))
    state = {'status':'running', 'started_at':datetime.now(timezone.utc).isoformat(),
             'source_run':str(source), 'task_ids':list(selected_ids), 'model':'qwen3.5-plus',
             'original_attempt_timeout_seconds':900, 'new_attempt_timeout_seconds':1800,
             'agent_call_cap':50, 'inner_call_cap':12, 'step_limit':45, 'held_out_opened':False,
             'assessments':[], 'limitations':['Two repeated cases cannot establish general success.',
               'Different request latency and live site conditions confound the comparison.',
               'Automated checking is fallible; submission alone is not verified task success.']}
    checkpoint = lambda: save(out/'summary.json', state)
    checkpoint()
    try:
        agents = development.run(source_summary['website'], contract, tasks, out/'agents',
                                 attempt_timeout=1800, queue_timeout=3700)
        records = {x['task_id']:x for x in json.loads((agents/'summary.json').read_text()).get('attempts', [])}
        state['status'] = 'assessment_running'
        checkpoint()
        for task in tasks:
            item = records.get(task['task_id'], {})
            script = agents/task['task_id']/'qwen3.5-plus/agent/final_script.py'
            if (item.get('trajectory_status') == 'Submitted' and item.get('process_exit') == 0
                    and script.is_file() and not script.is_symlink() and digest(script) == item.get('script_sha256')):
                assessment = evaluate_case(source_summary['website'], contract, task, script, out/'assessments'/task['task_id'])
            else:
                assessment = {'task_id':task['task_id'], 'status':'not_assessable',
                              'reason':'no submitted hash-matched script'}
            state['assessments'].append(assessment)
            checkpoint()
        state.update(status='complete_exploratory', finished_at=datetime.now(timezone.utc).isoformat())
        checkpoint()
    except Exception as exc:
        state.update(status='stopped_error', error_type=type(exc).__name__, error_message=str(exc)[:500])
        checkpoint()
        raise

if __name__ == '__main__':
    main()
