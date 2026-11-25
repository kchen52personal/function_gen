#!/usr/bin/env python3
"""Continue one interrupted adaptive run from a hash-checked development inventory.

The source directory remains immutable. This is a continuation with the current
runtime and reference prompt, not a replay of the exact historical software.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4

import adaptive
import development
from evaluate import evaluate_case
from synthesis import synthesize

PROJECT = Path('/shared/hans/research/website_tool_suite_generator_v1')
WORKSPACE = Path('/shared/hans')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, value):
    temporary = path.with_name('.' + path.name + '.' + uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def load_recovery(manifest_path):
    manifest = read(manifest_path)
    if manifest.get('status') != 'inventory_only_not_assessed' or manifest.get('held_out_opened_by_inventory') is not False:
        raise ValueError('unsupported or invalid recovery manifest')
    source = Path(manifest['source_run']).resolve(strict=True)
    if not source.is_relative_to(WORKSPACE):
        raise ValueError('source is outside workspace')
    state_path = source / 'summary.json'
    batch_root = source / 'development_rounds' / f"round_{manifest['round']:02d}"
    batch_path = batch_root / 'summary.json'
    if digest(state_path) != manifest.get('source_summary_sha256') or digest(batch_path) != manifest.get('batch_summary_sha256'):
        raise ValueError('source summary changed since inventory')
    state, batch = read(state_path), read(batch_path)
    if state.get('held_opened') is not False or state.get('checkpoints'):
        raise ValueError('source has opened held tasks or contains checkpoints')
    if any(x.get('status') == 'model_agreement' for x in state.get('development_attempts', [])):
        raise ValueError('source has prior agreements requiring separate recovery logic')
    replacement_path = source / 'replacements' / f"round_{manifest['round']:02d}" / 'tasks.json'
    original_tasks = read(replacement_path).get('tasks')
    if not isinstance(original_tasks, list) or not original_tasks:
        raise ValueError('replacement task manifest missing')
    tasks = {item['task_id']: item for item in original_tasks}
    batch_tasks = batch.get('tasks', [])
    if len(tasks) != len(original_tasks) or len(batch_tasks) != len(tasks):
        raise ValueError('replacement task manifest has duplicates or different size')
    if not all(isinstance(key, str) and re.fullmatch(r'[A-Za-z0-9_-]+', key) for key in tasks):
        raise ValueError('replacement task manifest has unsafe task IDs')
    for entry in batch_tasks:
        task = tasks.get(entry.get('task_id'))
        if not task or task.get('parameters') != entry.get('parameters'):
            raise ValueError('batch parameters do not match replacement manifest')
    candidates = manifest.get('candidates', [])
    ids = [entry.get('task_id') for entry in candidates]
    if len(ids) != len(tasks) or len(set(ids)) != len(ids) or set(ids) != set(tasks):
        raise ValueError('inventory does not exactly cover batch tasks')
    for entry in candidates:
        if entry.get('assessment') != 'not_run':
            raise ValueError('inventory unexpectedly contains an assessment')
        if 'script_path' in entry:
            path = Path(entry['script_path'])
            allowed = batch_root / entry['task_id'] / 'qwen3.5-plus'
            if path.is_symlink() or not path.resolve(strict=True).is_relative_to(allowed.resolve(strict=True)):
                raise ValueError('candidate is outside its attempt or is a symlink')
            if digest(path) != entry.get('script_sha256'):
                raise ValueError('candidate hash changed after inventory')
    deadline = datetime.fromisoformat(state['started_at']) + timedelta(seconds=state['limits']['overall_seconds'])
    return source, state, tasks, candidates, deadline, digest(replacement_path)


def load_prior(prior_path, manifest_path, source, candidates, reference_model):
    """Carry only completed candidate assessments, not startup failures."""
    prior_path = Path(prior_path).resolve(strict=True)
    if not prior_path.is_relative_to(WORKSPACE) or prior_path.is_relative_to(source):
        raise ValueError('prior summary must be separate and inside workspace')
    prior_hash = digest(prior_path)
    prior = read(prior_path)
    if (prior.get('status') != 'stopped_no_frozen_checkpoint' or prior.get('held_opened') is not False
            or prior.get('checkpoints') or prior.get('machine_agreed_development_count') != 0):
        raise ValueError('unsupported prior continuation state')
    if (prior.get('source_run') != str(source) or prior.get('recovery_manifest') != str(manifest_path)
            or prior.get('recovery_manifest_sha256') != digest(manifest_path)
            or prior.get('reference_model') != reference_model):
        raise ValueError('prior continuation provenance or model does not match')
    candidate_map = {entry['task_id']: entry for entry in candidates}
    carried, startup_errors = {}, []
    for result in prior.get('assessments', []):
        task_id = result.get('task_id')
        candidate = candidate_map.get(task_id)
        if candidate is None or task_id in carried or any(x.get('task_id') == task_id for x in startup_errors):
            raise ValueError('invalid or duplicate prior assessment')
        if 'script_path' in candidate:
            if (result.get('status') not in ('fail', 'inconclusive', 'not_assessable')
                    or result.get('script_path') != candidate['script_path']
                    or result.get('script_sha256') != candidate['script_sha256']
                    or result.get('candidate_origin') != candidate['candidate_origin']):
                raise ValueError('prior candidate assessment does not match inventory')
            carried[task_id] = {**result, 'carried_from_summary': str(prior_path)}
        else:
            if result.get('status') != 'not_assessable' or result.get('error_type') != 'FileNotFoundError':
                raise ValueError('only pre-attempt missing-directory errors can be retried')
            startup_errors.append(result)
    if set(carried) != {entry['task_id'] for entry in candidates if 'script_path' in entry}:
        raise ValueError('prior continuation has incomplete recovered-candidate assessments')
    if digest(prior_path) != prior_hash:
        raise ValueError('prior summary changed during loading')
    return prior_path, prior_hash, carried, startup_errors


def execute(manifest_path, output, *, reference_model, prior_continuation=None):
    manifest_path = Path(manifest_path).resolve(strict=True)
    output = Path(output).resolve(strict=False)
    if not manifest_path.is_relative_to(WORKSPACE) or not output.is_relative_to(WORKSPACE):
        raise ValueError('paths must be under /shared/hans')
    source, original, tasks, candidates, deadline, tasks_sha = load_recovery(manifest_path)
    if output.is_relative_to(source) or output == source:
        raise ValueError('continuation output must be separate from source')
    prior = load_prior(prior_continuation, manifest_path, source, candidates, reference_model) if prior_continuation else None
    output.mkdir(parents=True, exist_ok=False)
    for name in ('.config', '.cache', '.tmp'):
        (output / name).mkdir()
    os.environ.update(XDG_CONFIG_HOME=str(output/'.config'), XDG_CACHE_HOME=str(output/'.cache'),
                      TMPDIR=str(output/'.tmp'), PLAYWRIGHT_BROWSERS_PATH=str(PROJECT/'.playwright-browsers'))
    state = {'status': 'recovering_development', 'source_run': str(source),
             'recovery_manifest': str(manifest_path), 'recovery_manifest_sha256': digest(manifest_path),
             'replacement_tasks_sha256': tasks_sha, 'website': original['website'], 'idea': original['idea'],
             'started_at': datetime.now(timezone.utc).isoformat(), 'original_deadline': deadline.isoformat(),
             'held_opened': False, 'reference_model': reference_model, 'development_model': 'qwen3.5-plus',
             'source_previous_attempts': len(original.get('development_attempts', [])),
             'source_previous_agreements': 0, 'assessments': [], 'checkpoints': [],
             'limitations': ['Continuation uses the current runtime/reference prompt, not a bit-identical replay.',
                'An unsubmitted smoke draft is a candidate, never an automatic success.',
                'Model agreement is fallible and not independent human verification.',
                'Repeated held-case checkpoint scores are exploratory.',
                'Browser networking lacks hard read-only and network-allowlist enforcement.']}
    if prior:
        prior_path, prior_sha, carried, startup_errors = prior
        state['prior_continuation_summary'] = str(prior_path)
        state['prior_continuation_sha256'] = prior_sha
        state['assessments'] = list(carried.values())
        state['prior_startup_errors'] = startup_errors
    else:
        carried = {}
    summary = output / 'summary.json'
    checkpoint = lambda: save(summary, state)
    checkpoint()
    approved, ready = [], []
    contract = original['contract']
    def remaining():
        return max(0, (deadline - datetime.now(timezone.utc)).total_seconds())
    def assess(task, path, out):
        try:
            return evaluate_case(original['website'], contract, task, path, out,
                                 reference_model=reference_model, reference_calls=12)
        except Exception as exc:
            return {'task_id': task['task_id'], 'status': 'inconclusive',
                    'reason': 'assessment raised an exception', 'error_type': type(exc).__name__}
    def make_checkpoint():
        n = len(approved)
        base = output / 'checkpoints' / f'checkpoint_{n:02d}'
        entry = {'development_agreement_count': n, 'status': 'synthesis_running',
                 'source_task_ids': [x['task']['task_id'] for x in approved], 'held_results': []}
        state['checkpoints'].append(entry)
        state['status'] = 'checkpoint_synthesis_running'; checkpoint()
        try:
            examples = []
            for item in approved:
                if digest(item['path']) != item['sha256']:
                    raise ValueError('source script changed')
                examples.append({'task': item['task'], 'script_sha256': item['sha256'],
                                 'script': item['path'].read_text(encoding='utf-8')})
            response = synthesize(original['website'], contract, examples, base/'synthesis')
            candidate = base/'synthesis/candidate.py'
            if response.get('status') != 'candidate_ready' or digest(candidate) != response.get('candidate_sha256'):
                entry['status'] = 'synthesis_rejected'; checkpoint(); return
            entry['development_checks'] = []
            for item in approved:
                if not remaining():
                    entry['status'] = 'stopped_budget'; checkpoint(); return
                entry['development_checks'].append(assess(item['task'], candidate, base/'development_checks'/item['task']['task_id']))
                checkpoint()
            # Match the original adaptive pipeline's checkpoint gate exactly.
            if not any(x.get('status') == 'model_agreement' for x in entry['development_checks']):
                entry['status'] = 'no_development_agreement'; checkpoint(); return
            frozen = base/'frozen.py'
            shutil.copyfile(candidate, frozen)
            if digest(frozen) != response['candidate_sha256']:
                raise ValueError('frozen hash mismatch')
            entry.update(status='frozen_before_any_held', sha256=digest(frozen), path=str(frozen),
                         frozen_at=datetime.now(timezone.utc).isoformat())
            ready.append(entry); checkpoint()
        except Exception as exc:
            entry.update(status='checkpoint_error', error_type=type(exc).__name__, error_message=str(exc)[:300]); checkpoint()
    def consider(task, path, origin, expected_sha):
        if digest(path) != expected_sha:
            raise ValueError('candidate hash changed before assessment')
        state.update(status='development_assessment_running', current={'task_id': task['task_id'], 'candidate_origin': origin}); checkpoint()
        verdict = assess(task, path, output/'development_checks'/task['task_id'])
        if digest(path) != expected_sha:
            verdict = {'task_id': task['task_id'], 'status': 'fail', 'reason': 'candidate changed during assessment'}
        state['assessments'].append({'candidate_origin': origin, 'script_path': str(path),
                                     'script_sha256': expected_sha, **verdict})
        state.pop('current', None); checkpoint()
        if verdict.get('status') == 'model_agreement':
            approved.append({'task': task, 'path': path, 'sha256': expected_sha})
            state['machine_agreed_development_count'] = len(approved); checkpoint()
            if len(approved) >= 2 and remaining():
                make_checkpoint()
    try:
        for candidate in candidates:
            if not remaining(): break
            task = tasks[candidate['task_id']]
            if task['task_id'] in carried:
                continue
            if 'script_path' in candidate:
                consider(task, Path(candidate['script_path']), candidate['candidate_origin'], candidate['script_sha256'])
                continue
            state.update(status='development_agent_running', current={'task_id': task['task_id']}); checkpoint()
            run_root = output/'development_round_02_remaining'/task['task_id']
            run_root.parent.mkdir(parents=True, exist_ok=True)
            attempt_seconds = min(900, max(1, int(remaining())))
            try:
                development.run(original['website'], contract, [task], run_root,
                    attempt_timeout=attempt_seconds, queue_timeout=min(attempt_seconds+40, max(1, int(remaining()))),
                    model='qwen3.5-plus')
                records = read(run_root/'summary.json').get('attempts', [])
                record = next((x for x in records if x.get('task_id') == task['task_id']), {})
                script, origin = adaptive.select_development_candidate(run_root, task, record)
            except Exception as exc:
                state['assessments'].append({'task_id': task['task_id'], 'status': 'not_assessable',
                    'reason': 'development attempt raised an exception', 'error_type': type(exc).__name__})
                state.pop('current', None); checkpoint(); continue
            if script is None:
                state['assessments'].append({'task_id': task['task_id'], 'status': 'not_assessable',
                    'reason': 'no submitted or hash-checked candidate', 'candidate_origin': origin})
                state.pop('current', None); checkpoint()
            elif remaining():
                consider(task, script, origin, digest(script))
        state.pop('current', None)
        state['machine_agreed_development_count'] = len(approved)
        if not ready:
            state.update(status='stopped_no_frozen_checkpoint', finished_at=datetime.now(timezone.utc).isoformat()); checkpoint(); return state
        if not remaining():
            state.update(status='stopped_budget_before_held', finished_at=datetime.now(timezone.utc).isoformat()); checkpoint(); return state
        state.update(status='all_checkpoints_frozen_before_held', generation_closed=True,
                     generation_closed_at=datetime.now(timezone.utc).isoformat()); checkpoint()
        held_path = source/'tasks/held_out/tasks.json'
        if digest(held_path) != original['split_hashes']['held_out']:
            raise ValueError('held manifest hash changed')
        held = read(held_path)['tasks']
        if len(held) != 4:
            raise ValueError('expected four held tasks')
        state.update(status='held_checks_running', held_opened=True); checkpoint()
        for entry in ready:
            path = Path(entry['path'])
            for task in held:
                if not remaining():
                    state.update(status='stopped_budget_during_held', finished_at=datetime.now(timezone.utc).isoformat()); checkpoint(); return state
                if digest(path) != entry['sha256']:
                    raise ValueError('frozen checkpoint changed')
                entry['held_results'].append(assess(task, path, output/'checkpoints'/f"checkpoint_{entry['development_agreement_count']:02d}"/'held_checks'/task['task_id']))
                checkpoint()
            entry['held_model_agreements'] = sum(x.get('status') == 'model_agreement' for x in entry['held_results'])
            entry['status'] = 'held_assessed_exploratory'; checkpoint()
        state.update(status='complete_exploratory_checkpoint_comparison', finished_at=datetime.now(timezone.utc).isoformat()); checkpoint()
        return state
    except Exception as exc:
        state.update(status='stopped_error', error_type=type(exc).__name__, error_message=str(exc)[:500],
                     finished_at=datetime.now(timezone.utc).isoformat()); checkpoint(); raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--reference-model', required=True)
    parser.add_argument('--prior-continuation', type=Path)
    args = parser.parse_args()
    execute(args.manifest, args.output, reference_model=args.reference_model, prior_continuation=args.prior_continuation)


if __name__ == '__main__':
    main()
