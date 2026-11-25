#!/usr/bin/env python3
"""Freeze a function from submitted development scripts, then execute held cases.

Continuation only; does not modify the source run or claim semantic correctness.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil

import adaptive
from public_environment import IsolatedPublicEnvironment
from synthesis import trajectory_excerpt


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('config', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    root = Path('/shared/hans').resolve()
    source = args.source.resolve(strict=True)
    output = args.output.resolve()
    if not source.is_relative_to(root) or not output.is_relative_to(root) or output.is_relative_to(source):
        raise ValueError('invalid source or continuation output')
    old, config = read(source/'summary.json'), read(args.config)
    if old['status'] != 'stopped_insufficient_submitted_trajectories' or old['held_opened'] or old['checkpoints']:
        raise ValueError('source is not an eligible unopened stopped run')
    if config['website'] != old['website'] or config['functionality'] != old['idea']:
        raise ValueError('source and configuration differ')
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    os.chmod(output, 0o700)
    state = {'status':'inventory', 'source_run':str(source), 'held_opened':False,
             'held_results':[], 'started_at':datetime.now(timezone.utc).isoformat(),
             'limitations':['WebWright submissions are not independently verified.',
                            'Held results are execution-only, not accuracy.',
                            'Creating blank notes can persist in the user account.']}
    def save(): adaptive.save(output/'summary.json', state)
    save()
    env = None
    try:
        examples = []
        for result in old['development_attempts']:
            if result.get('candidate_origin') != 'submitted' or result.get('status') != 'webwright_submitted':
                continue
            round_num = result['round']
            batch = source/'development_rounds'/f'round_{round_num:02d}'
            manifest = source/'tasks/development/tasks.json' if round_num == 0 else source/'replacements'/f'round_{round_num:02d}'/'tasks.json'
            task = next(x for x in read(manifest)['tasks'] if x['task_id'] == result['task_id'])
            record = next(x for x in read(batch/'summary.json')['attempts'] if x['task_id'] == task['task_id'])
            path, origin = adaptive.select_development_candidate(batch, task, record)
            if origin != 'submitted' or path is None: raise ValueError('submitted script failed hash validation')
            examples.append({'task':task,'script_sha256':adaptive.digest(path),
                'assessment':{'status':'webwright_submitted','reason':'Not independently verified'},
                'trajectory':trajectory_excerpt(path.parent),'script':path.read_text(encoding='utf-8')})
        if len(examples) != 6 or len({x['task']['task_id'] for x in examples}) != 6:
            raise ValueError('expected exactly six distinct submitted examples')
        state['source_task_ids'] = [x['task']['task_id'] for x in examples]
        state['source_script_sha256'] = {x['task']['task_id']:x['script_sha256'] for x in examples}
        state['status'] = 'synthesis_running'; save()
        env = IsolatedPublicEnvironment(output, old['website'], resource_hosts=config['resource_hosts'],
            session_directory=config['session_directory'], allow_authorized_creation=True,
            raw_request_argument=True)
        result = env.synthesize(old['website'], old['contract'], examples, output/'synthesis')
        candidate = output/'synthesis/candidate.py'
        state['synthesis_result'] = result
        if result.get('status') != 'candidate_ready' or adaptive.digest(candidate) != result.get('candidate_sha256'):
            state['status'] = 'synthesis_rejected'; save(); return
        frozen = output/'frozen.py'
        shutil.copyfile(candidate, frozen)
        sha = adaptive.digest(frozen)
        if sha != result['candidate_sha256']: raise ValueError('freeze hash mismatch')
        state.update(status='frozen_before_held', frozen_sha256=sha,
                     frozen_at=datetime.now(timezone.utc).isoformat()); save()
        held_path = source/'tasks/held_out/tasks.json'
        if adaptive.digest(held_path) != old['split_hashes']['held_out']:
            raise ValueError('held manifest hash mismatch')
        held = read(held_path)['tasks']
        if len(held) != 3: raise ValueError('expected three held cases')
        state.update(status='held_running',held_opened=True); save()
        for task in held:
            if adaptive.digest(frozen) != sha: raise ValueError('frozen script changed')
            try:
                item = env.run_ungraded_case(task, frozen, output/'held_checks'/task['task_id'])
            except Exception as exc:
                item = {'task_id':task['task_id'],'status':'execution_error','error_type':type(exc).__name__}
            state['held_results'].append(item); save()
        state.update(status='complete_held_outputs_ungraded',finished_at=datetime.now(timezone.utc).isoformat()); save()
    except Exception as exc:
        state.update(status='stopped_error',error_type=type(exc).__name__,error_message=str(exc)[:300]); save()
        raise
    finally:
        if env: env.close()


if __name__ == '__main__': main()
