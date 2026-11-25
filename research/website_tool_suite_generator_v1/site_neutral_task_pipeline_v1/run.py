#!/usr/bin/env python3
"""Bounded URL+idea -> development -> synthesis -> held-out evaluation.

The website and functionality idea are the only required user inputs. This
pipeline is site-neutral, but access, relevance and independent semantic
correctness cannot be guaranteed for an arbitrary site.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sys
from uuid import uuid4

from factory import PROJECT, create_taskset, validate_site
import development
from evaluate import DEFAULT_REFERENCE_MODEL, evaluate_case
from synthesis import synthesize
from reconnaissance import reconnaissance

OUTPUT_PARENT = PROJECT / 'automated_runs'

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def save(path, state):
    temp = path.with_name('.' + path.name + '.' + uuid4().hex + '.tmp')
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temp, path)

def agent_records(root):
    path = root / 'summary.json'
    if not path.is_file():
        return {}
    state = json.loads(path.read_text(encoding='utf-8'))
    return {x['task_id']:x for x in state.get('attempts', [])}

def submitted_script(root, task, records):
    item = records.get(task['task_id'])
    path = root / task['task_id'] / 'qwen3.5-plus/agent/final_script.py'
    if (not item or item.get('trajectory_status') != 'Submitted' or item.get('process_exit') != 0
            or not path.is_file() or path.is_symlink() or digest(path) != item.get('script_sha256')):
        return None
    return path

def execute(url, idea, output):
    validate_site(url, idea)
    output.mkdir(parents=True, exist_ok=False)
    for name in ('.config', '.cache', '.tmp'):
        (output/name).mkdir()
    os.environ['XDG_CONFIG_HOME'] = str(output/'.config')
    os.environ['XDG_CACHE_HOME'] = str(output/'.cache')
    os.environ['TMPDIR'] = str(output/'.tmp')
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(PROJECT/'.playwright-browsers')
    state = {'status':'reconnaissance_running', 'started_at':datetime.now(timezone.utc).isoformat(),
        'website':url, 'idea':idea, 'held_opened':False, 'manual_runtime_review':False,
        'reference_model':DEFAULT_REFERENCE_MODEL,
        'development':[], 'held_out':[], 'retry_cap':3,
        'limits':{'pages':3,'agent_calls_per_attempt':50,'inner_calls_per_attempt':12,
                  'per_agent_seconds':900,'development_queue_seconds':7500,'retry_queue_seconds':3000},
        'limitations':['Only public read-only tasks are intended; browser networking is not hard read-only enforced.',
            'Access, page contents, geography, authorization and anti-automation restrictions may prevent execution.',
            'Model-generated tasks and a blind reference are not ground truth; the code writer and reference are both Qwen models and correlated errors remain possible.',
            'An automated agreement is evidence for tested cases, not proof of correctness.',
            'Exact distinct inputs do not establish semantic independence.',
            'Model task generator sees all 12 proposals before the split; code writer sees development only before freeze.']}
    summary = output/'summary.json'
    def checkpoint():
        save(summary,state)
    checkpoint()
    try:
        recon = asyncio.run(asyncio.wait_for(reconnaissance(url, output/'recon', max_pages=3), timeout=240))
        if not any(page.get('http_status') == 200 for page in recon.get('pages', [])):
            state.update(status='stopped_site_not_reachable', reason='no successful public page in reconnaissance')
            checkpoint()
            return state
        state['status'] = 'task_generation_running'
        checkpoint()
        contract, split = create_taskset(url, idea, recon, output/'tasks')
        state.update(status='development_agents_running', contract=contract,
                     split_hashes=split['manifest_sha256'])
        checkpoint()
        development_path = output/'tasks/development/tasks.json'
        if digest(development_path) != split['manifest_sha256']['development']:
            raise ValueError('development manifest changed')
        tasks = json.loads(development_path.read_text(encoding='utf-8'))['tasks']
        first = development.run(url, contract, tasks, output/'development_agents')
        records = agent_records(first)
        state['status'] = 'development_checks_running'
        checkpoint()
        approved, feedback = {}, {}
        for task in tasks:
            source = submitted_script(first, task, records)
            if source is None:
                assessment = {'task_id':task['task_id'], 'status':'fail', 'reason':'agent did not submit a hash-matched script'}
            else:
                assessment = evaluate_case(url, contract, task, source, output/'development_checks'/task['task_id'])
                if assessment['status'] == 'model_agreement':
                    approved[task['task_id']] = {'path':source,'sha256':digest(source)}
            feedback[task['task_id']] = assessment['reason']
            state['development'].append({'phase':'initial', **assessment})
            checkpoint()
        remaining = [task for task in tasks if task['task_id'] not in approved]
        retry = remaining[:state['retry_cap']]
        state.update(status='development_retries_running', retry_selected_ids=[x['task_id'] for x in retry])
        checkpoint()
        retry_root = development.run(url, contract, retry, output/'development_retry', feedback=feedback,
                                     queue_timeout=3000)
        if retry_root:
            retry_records = agent_records(retry_root)
            for task in retry:
                source = submitted_script(retry_root, task, retry_records)
                if source is None:
                    assessment = {'task_id':task['task_id'], 'status':'fail', 'reason':'retry did not submit a hash-matched script'}
                else:
                    assessment = evaluate_case(url, contract, task, source, output/'retry_checks'/task['task_id'])
                    if assessment['status'] == 'model_agreement':
                        approved[task['task_id']] = {'path':source,'sha256':digest(source)}
                state['development'].append({'phase':'retry', **assessment})
                checkpoint()
        state['machine_approved_development_count'] = len(approved)
        state['unresolved_task_ids'] = [x['task_id'] for x in tasks if x['task_id'] not in approved]
        if len(approved) < 2:
            state.update(status='stopped_insufficient_development_evidence', held_opened=False)
            checkpoint()
            return state
        examples = []
        for task in tasks:
            item = approved.get(task['task_id'])
            if item:
                if digest(item['path']) != item['sha256']:
                    raise ValueError('development source changed before synthesis')
                examples.append({'task':task, 'script_sha256':item['sha256'],
                                 'script':item['path'].read_text(encoding='utf-8')})
        state['status'] = 'synthesis_running'
        checkpoint()
        synthesis = synthesize(url, contract, examples, output/'synthesis')
        candidate = output/'synthesis/candidate.py'
        if synthesis.get('status') != 'candidate_ready' or digest(candidate) != synthesis.get('candidate_sha256'):
            state.update(status='stopped_synthesis_not_usable', held_opened=False)
            checkpoint()
            return state
        state['status'] = 'generalized_development_checks_running'
        state['generalized_development'] = []
        checkpoint()
        for task in tasks:
            state['generalized_development'].append(evaluate_case(url, contract, task, candidate,
                output/'generalized_development'/task['task_id']))
            checkpoint()
        if not any(x['status']=='model_agreement' for x in state['generalized_development']):
            state.update(status='stopped_no_development_agreement', held_opened=False)
            checkpoint()
            return state
        (output/'frozen').mkdir()
        frozen = output/'frozen/site_function.py'
        shutil.copyfile(candidate,frozen)
        if digest(frozen) != synthesis['candidate_sha256']:
            raise ValueError('frozen function hash mismatch')
        state.update(status='frozen_before_held', frozen_sha256=digest(frozen),
                     frozen_at=datetime.now(timezone.utc).isoformat())
        checkpoint()  # Save before opening or hashing the held manifest.
        held_path = output/'tasks/held_out/tasks.json'
        if digest(held_path) != split['manifest_sha256']['held_out']:
            raise ValueError('held-out manifest changed')
        held = json.loads(held_path.read_text(encoding='utf-8'))['tasks']
        if len(held) != 4:
            raise ValueError('expected four held-out tasks')
        state.update(status='held_checks_running', held_opened=True)
        checkpoint()
        for task in held:
            if digest(frozen) != state['frozen_sha256']:
                raise ValueError('frozen function changed')
            state['held_out'].append(evaluate_case(url, contract, task, frozen, output/'held_out'/task['task_id']))
            checkpoint()
        state.update(status='complete_automated_assessment_only',
            held_model_agreements=sum(x['status']=='model_agreement' for x in state['held_out']),
            finished_at=datetime.now(timezone.utc).isoformat())
        checkpoint()
        return state
    except Exception as exc:
        state.update(status='stopped_error', error_type=type(exc).__name__, error_message=str(exc)[:500],
                     finished_at=datetime.now(timezone.utc).isoformat())
        checkpoint()
        raise

def main():
    from adaptive import execute as execute_adaptive
    parser = argparse.ArgumentParser(description='Site-neutral website+functionality tool pipeline')
    parser.add_argument('website', help='public HTTPS website URL')
    parser.add_argument('functionality', help='short read-only functionality idea')
    parser.add_argument('--final-only', action='store_true', help='Create one function after the development target is reached')
    parser.add_argument('--webwright-only', action='store_true', help='Use WebWright submissions only; held runs remain ungraded')
    parser.add_argument('--max-replacement-rounds', type=int, default=2, help='Maximum replacement rounds (0–5)')
    args = parser.parse_args()
    name = 'site_neutral_' + datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8]
    output = OUTPUT_PARENT/name
    print('Pipeline output:', output, flush=True)
    execute_adaptive(args.website, args.functionality, output,
                     final_only=args.final_only or not args.webwright_only, webwright_only=args.webwright_only,
                     verify_development=not args.webwright_only,
                     max_rounds=args.max_replacement_rounds)

if __name__ == '__main__':
    def interrupted(signum, frame):
        raise TimeoutError(f'pipeline received signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    main()
