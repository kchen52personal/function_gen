#!/usr/bin/env python3
"""Synthesize from a stopped run's submitted development trajectories only.

An exploratory continuation: original files are immutable, no interactive
assistant is in the runtime loop, and held inputs open only after code freeze.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import time

import adaptive
from evaluate import DEFAULT_REFERENCE_MODEL, evaluate_case
from factory import PROJECT
from synthesis import synthesize, trajectory_excerpt

WORKSPACE = Path('/shared/hans').resolve()

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

def collect(source, continuation):
    state = read(source/'summary.json')
    resumed = read(continuation/'summary.json')
    if state.get('held_opened') is not False or resumed.get('held_opened') is not False:
        raise ValueError('source or continuation already opened held tasks')
    if resumed.get('source_run') != str(source) or resumed.get('status') != 'stopped_no_frozen_checkpoint':
        raise ValueError('unsupported continuation provenance or status')
    if resumed.get('checkpoints'):
        raise ValueError('continuation already has checkpoints')
    initial = source/'tasks/development/tasks.json'
    if adaptive.digest(initial) != state['split_hashes']['development']:
        raise ValueError('development manifest hash mismatch')
    task_map = {item['task_id']:item for item in read(initial)['tasks']}
    for path in sorted((source/'replacements').glob('round_*/tasks.json')):
        for item in read(path)['tasks']:
            if item['task_id'] in task_map: raise ValueError('duplicate task ID')
            task_map[item['task_id']] = item
    assessments = {item['task_id']:item for item in state.get('development_attempts',[])}
    assessments.update({item['task_id']:item for item in resumed.get('assessments',[])})
    examples, seen = [], set()
    for summary in sorted((source/'development_rounds').glob('round_*/summary.json')):
        batch = read(summary)
        for record in batch.get('attempts',[]):
            if record.get('trajectory_status') != 'Submitted' or record.get('process_exit') != 0:
                continue
            tid = record.get('task_id')
            if not isinstance(tid,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',tid) or tid in seen:
                raise ValueError('invalid or duplicate submitted task ID')
            task = task_map.get(tid)
            batch_task = next((x for x in batch.get('tasks',[]) if x.get('task_id') == tid),None)
            if not task or not batch_task or task['parameters'] != batch_task.get('parameters'):
                raise ValueError('submitted task does not match development manifest')
            script, origin = adaptive.select_development_candidate(summary.parent,task,record)
            if script is None or origin != 'submitted':
                raise ValueError('submitted script missing or hash mismatch')
            verdict = assessments.get(tid,{'status':'not_assessed','reason':'no completed assessment'})
            examples.append({'task':task, 'script_path':str(script), 'script_sha256':adaptive.digest(script),
                'assessment':{'status':verdict.get('status'),'reason':verdict.get('reason','')},
                'trajectory':trajectory_excerpt(script.parent), 'script':script.read_text(encoding='utf-8')})
            seen.add(tid)
    if len(examples)<2: raise ValueError('fewer than two hash-checked WebWright submissions')
    return state, examples

def execute(source, continuation, output, *, dev_limit=3, evaluate_held=False):
    source, continuation, output = source.resolve(strict=True), continuation.resolve(strict=True), output.resolve()
    if not all(p.is_relative_to(WORKSPACE) for p in (source,continuation,output)):
        raise ValueError('paths must remain under workspace')
    if output.is_relative_to(source) or output.is_relative_to(continuation):
        raise ValueError('output must be separate from original runs')
    original, examples = collect(source,continuation)
    output.mkdir(parents=True,exist_ok=False)
    for name in ('.config','.cache','.tmp'): (output/name).mkdir()
    os.environ.update(XDG_CONFIG_HOME=str(output/'.config'),XDG_CACHE_HOME=str(output/'.cache'),
                      TMPDIR=str(output/'.tmp'),PLAYWRIGHT_BROWSERS_PATH=str(PROJECT/'.playwright-browsers'))
    state = {'status':'synthesis_running','source_run':str(source),'continuation':str(continuation),
        'source_summary_sha256':adaptive.digest(source/'summary.json'),
        'continuation_summary_sha256':adaptive.digest(continuation/'summary.json'),
        'started_at':datetime.now(timezone.utc).isoformat(), 'held_opened':False,
        'reference_model':DEFAULT_REFERENCE_MODEL, 'submitted_trajectory_count':len(examples),
        'source_trajectories':[{'task_id':x['task']['task_id'],'sha256':x['script_sha256'],
                                'assessment':x['assessment']} for x in examples],
        'development_checks':[], 'held_checks':[],
        'limitations':['Submitted trajectories and smoke-valid candidate code are not proof of correctness.',
          'The trajectory excerpts are bounded and do not capture every action or screenshot.',
          'The Qwen writer and independent Qwen reference may have correlated errors.',
          'Model agreement is fallible and held-out results are exploratory.',
          'Browser networking has no hard site allowlist or read-only enforcement.']}
    checkpoint = lambda: adaptive.save(output/'summary.json',state)
    checkpoint()
    try:
        for item in examples:
            if adaptive.digest(Path(item['script_path'])) != item['script_sha256']:
                raise ValueError('source script changed before synthesis')
        model_examples = [{k:v for k,v in item.items() if k != 'script_path'} for item in examples]
        result = synthesize(original['website'],original['contract'],model_examples,output/'synthesis')
        candidate = output/'synthesis/candidate.py'
        if result.get('status') != 'candidate_ready' or adaptive.digest(candidate) != result.get('candidate_sha256'):
            state['status']='stopped_synthesis_rejected'; checkpoint(); return state
        state.update(status='development_checks_running',candidate_sha256=adaptive.digest(candidate)); checkpoint()
        indices = sorted({0, len(examples)//2, len(examples)-1})[:dev_limit]
        state['development_check_task_ids'] = [examples[i]['task']['task_id'] for i in indices]; checkpoint()
        for i in indices:
            task = examples[i]['task']
            try:
                assessment = evaluate_case(original['website'],original['contract'],task,candidate,
                    output/'development_checks'/task['task_id'])
            except Exception as exc:
                assessment = {'task_id':task['task_id'],'status':'inconclusive','format_valid':False,
                              'reason':'assessment raised exception','error_type':type(exc).__name__}
            state['development_checks'].append(assessment); checkpoint()
        if sum(x.get('format_valid') is True for x in state['development_checks'])<2:
            state['status']='stopped_insufficient_format_valid_development'; checkpoint(); return state
        frozen = output/'frozen.py';shutil.copyfile(candidate,frozen)
        if adaptive.digest(frozen)!=state['candidate_sha256']: raise ValueError('frozen hash mismatch')
        state.update(status='frozen_before_held',generation_closed=True,frozen_sha256=adaptive.digest(frozen),
                     frozen_at=datetime.now(timezone.utc).isoformat());checkpoint()
        if not evaluate_held:
            state.update(status='frozen_development_only',finished_at=datetime.now(timezone.utc).isoformat())
            checkpoint(); return state
        held_path = source/'tasks/held_out/tasks.json'
        if adaptive.digest(held_path)!=original['split_hashes']['held_out']:
            raise ValueError('held manifest changed')
        held = read(held_path)['tasks']
        if len(held)!=4: raise ValueError('expected four held tasks')
        state.update(status='held_checks_running',held_opened=True); checkpoint()
        for task in held:
            if adaptive.digest(frozen)!=state['frozen_sha256']: raise ValueError('frozen code changed')
            try:
                assessment=evaluate_case(original['website'],original['contract'],task,frozen,output/'held_checks'/task['task_id'])
            except Exception as exc:
                assessment={'task_id':task['task_id'],'status':'inconclusive','format_valid':False,
                            'reason':'assessment raised exception','error_type':type(exc).__name__}
            state['held_checks'].append(assessment); checkpoint()
        state.update(status='complete_exploratory',
            held_model_agreements=sum(x['status']=='model_agreement' for x in state['held_checks']),
            finished_at=datetime.now(timezone.utc).isoformat());checkpoint();return state
    except Exception as exc:
        state.update(status='stopped_error',error_type=type(exc).__name__,error_message=str(exc)[:500],
                     finished_at=datetime.now(timezone.utc).isoformat());checkpoint();raise

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('source',type=Path);parser.add_argument('continuation',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--evaluate-held',action='store_true',help='After freeze, open and evaluate the held-out tasks')
    args=parser.parse_args();execute(args.source,args.continuation,args.output,evaluate_held=args.evaluate_held)

if __name__=='__main__':
    def interrupted(signum,frame): raise TimeoutError(f'continuation received signal {signum}')
    signal.signal(signal.SIGTERM,interrupted)
    main()
