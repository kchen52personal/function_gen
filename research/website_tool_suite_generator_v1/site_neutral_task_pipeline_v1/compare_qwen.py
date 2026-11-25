#!/usr/bin/env python3
"""Bounded, paired development-only comparison; never opens the held set."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import time
from uuid import uuid4

import adaptive
from api_timing import summarize
import development
from evaluate import evaluate_case

MODELS = ('qwen3.8-flash', 'qwen3.5-plus')

def write(path, value):
    temp = path.with_name('.' + path.name + '.' + uuid4().hex + '.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    os.replace(temp, path)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source_run', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--attempt-timeout-seconds',type=int,choices=(900,1800),default=1800)
    args = parser.parse_args()
    source = args.source_run.resolve()
    summary = json.loads((source/'summary.json').read_text())
    manifest = source/'tasks/development/tasks.json'
    expected = summary['split_hashes']['development']
    if digest(manifest) != expected:
        raise ValueError('development manifest hash mismatch')
    tasks = json.loads(manifest.read_text())['tasks']
    if len(tasks) != 8:
        raise ValueError('expected eight development tasks')
    chosen = [tasks[0], tasks[4]]
    contract = json.loads((source/'tasks/contract.json').read_text())
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    for name in ('.config', '.cache', '.tmp'):
        (out/name).mkdir()
    os.environ.update(XDG_CONFIG_HOME=str(out/'.config'), XDG_CACHE_HOME=str(out/'.cache'),
                      TMPDIR=str(out/'.tmp'), PLAYWRIGHT_BROWSERS_PATH=str(development.PROJECT/'.playwright-browsers'))
    state = {'status':'running', 'started_at':datetime.now(timezone.utc).isoformat(),
             'source_run':str(source), 'website':summary['website'], 'models':list(MODELS),
             'task_selection':'first and fifth development rows, fixed before comparison',
             'development_sha256':expected, 'task_ids':[t['task_id'] for t in chosen],
             'held_out_opened':False, 'attempts':[],
             'limits':{'agent_calls_per_attempt':50, 'inner_calls_per_attempt':12,
                       'seconds_per_attempt':args.attempt_timeout_seconds},
             'limitations':['Two development tasks are exploratory, not a reliable model ranking.',
                            'Sequential runs and concurrent experiments may change API/server load.',
                            'Automated model agreement is fallible, not a verified ground-truth pass.',
                            'No network allowlist or hard browser read-only enforcement.']}
    save = lambda: write(out/'summary.json', state)
    save()
    try:
        for index, task in enumerate(chosen):
            for model in (MODELS if index % 2 == 0 else tuple(reversed(MODELS))):
                state['current'] = {'task_id':task['task_id'], 'model':model,'phase':'development'}
                save()
                attempt_root = out / task['task_id'] / model / 'run'
                attempt_root.parent.mkdir(parents=True, exist_ok=True)
                start = time.monotonic()
                run_error = None
                try:
                    development.run(summary['website'], contract, [task], attempt_root,
                                    attempt_timeout=args.attempt_timeout_seconds,
                                    queue_timeout=args.attempt_timeout_seconds+40, model=model)
                except Exception as exc:
                    run_error = type(exc).__name__
                development_wall_seconds = round(time.monotonic()-start,2)
                try:
                    result = json.loads((attempt_root/'summary.json').read_text())
                except (ValueError,OSError,TypeError):
                    result = {}
                item = next((x for x in result.get('attempts', []) if x['task_id'] == task['task_id']), {})
                script, origin = adaptive.select_development_candidate(attempt_root,task,item,model=model)
                state['current'].update(phase='assessment', trajectory_status=item.get('trajectory_status'),
                                        process_exit=item.get('process_exit'),candidate_origin=origin,
                                        development_wall_seconds=development_wall_seconds)
                save()
                assessment_start = time.monotonic()
                if script is not None:
                    try:
                        assessment = evaluate_case(summary['website'], contract, task, script,
                                                   out/task['task_id']/model/'assessment')
                    except Exception as exc:
                        assessment = {'status':'inconclusive','reason':'assessment error',
                                      'error_type':type(exc).__name__}
                else:
                    assessment = {'status':'not_assessable', 'reason':'no submitted or smoke-checked candidate'}
                state['attempts'].append({'task_id':task['task_id'], 'model':model,
                    'trajectory_status':item.get('trajectory_status'), 'process_exit':item.get('process_exit'),
                    'agent_calls':item.get('agent_calls'), 'inner_tool_calls':item.get('inner_tool_calls'),
                    'candidate_origin':origin,'run_error_type':run_error,
                    'development_wall_seconds':development_wall_seconds,
                    'assessment_wall_seconds':round(time.monotonic()-assessment_start,2),
                    'api_timing':summarize(attempt_root/task['task_id']/model/'api_logs/stats'),
                    'assessment':assessment})
                save()
        state.update(status='complete_exploratory', finished_at=datetime.now(timezone.utc).isoformat())
        state.pop('current', None)
        save()
    except Exception as exc:
        state.update(status='stopped_error', error_type=type(exc).__name__, error_message=str(exc)[:500])
        save()
        raise

if __name__ == '__main__':
    def interrupted(signum, frame):
        raise TimeoutError(f'comparison received signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    main()
