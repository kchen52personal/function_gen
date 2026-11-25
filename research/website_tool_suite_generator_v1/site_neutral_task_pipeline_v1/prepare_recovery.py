#!/usr/bin/env python3
"""Inventory an interrupted development batch without opening held tasks or making API calls.

The output is an inventory, not an assessment or a resumed experiment. It
records existing submitted scripts and any valid unsubmitted smoke checkpoint
under their original hashes, and never changes the source run.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from adaptive import select_development_candidate

WORKSPACE = Path('/shared/hans')
TASK_ID = re.compile(r'^[A-Za-z0-9_-]+$')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(source, round_number):
    source = Path(source).resolve(strict=True)
    if not source.is_relative_to(WORKSPACE) or not source.is_dir():
        raise ValueError('source must be a run directory under /shared/hans')
    summary_path = source / 'summary.json'
    batch_root = source / 'development_rounds' / f'round_{round_number:02d}'
    batch_path = batch_root / 'summary.json'
    snapshots = {str(p): digest(p) for p in (summary_path, batch_path)}
    state = json.loads(summary_path.read_text(encoding='utf-8'))
    batch = json.loads(batch_path.read_text(encoding='utf-8'))
    if state.get('held_opened') is not False:
        raise ValueError('source cannot prove held inputs were unopened')
    tasks = batch.get('tasks')
    attempts = batch.get('attempts')
    if not isinstance(tasks, list) or not isinstance(attempts, list):
        raise ValueError('batch lacks tasks or attempts')
    task_ids = [x.get('task_id') for x in tasks if isinstance(x, dict)]
    if len(task_ids) != len(tasks) or len(task_ids) != len(set(task_ids)) or not all(
            isinstance(x, str) and TASK_ID.fullmatch(x) for x in task_ids):
        raise ValueError('invalid or duplicate task IDs')
    attempts_by_id = {}
    for attempt in attempts:
        task_id = attempt.get('task_id') if isinstance(attempt, dict) else None
        if task_id not in task_ids or task_id in attempts_by_id:
            raise ValueError('invalid or duplicate attempt IDs')
        attempts_by_id[task_id] = attempt
    items = []
    for task in tasks:
        task_id = task['task_id']
        record = attempts_by_id.get(task_id, {})
        script, origin = select_development_candidate(batch_root, task, record)
        item = {'task_id': task_id, 'candidate_origin': origin,
                'process_exit': record.get('process_exit'),
                'trajectory_status': record.get('trajectory_status'),
                'assessment': 'not_run'}
        if script is not None:
            item.update(script_path=str(script), script_sha256=digest(script))
        items.append(item)
    if any(digest(Path(name)) != sha for name, sha in snapshots.items()):
        raise ValueError('source summaries changed during inventory')
    return {'status': 'inventory_only_not_assessed', 'source_run': str(source),
            'round': round_number, 'website': state.get('website'),
            'source_summary_status': state.get('status'), 'source_summary_sha256': snapshots[str(summary_path)],
            'batch_summary_sha256': snapshots[str(batch_path)],
            'held_out_opened_by_inventory': False, 'model_calls': 0,
            'candidates': items,
            'limitation': 'Presence and hashes do not establish task success; every candidate requires assessment.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--round', type=int, required=True, choices=(0, 1, 2))
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    output = args.output.resolve(strict=False)
    if not output.is_relative_to(WORKSPACE) or output.is_relative_to(source):
        raise ValueError('output must be under /shared/hans and outside the source run')
    value = inventory(source, args.round)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps({'status': value['status'], 'output': str(output),
                      'candidate_count': sum('script_path' in x for x in value['candidates'])}))


if __name__ == '__main__':
    main()
