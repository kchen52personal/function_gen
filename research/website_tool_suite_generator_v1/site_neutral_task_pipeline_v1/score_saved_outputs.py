#!/usr/bin/env python3
"""Optional offline scoring against independently prepared gold results.

Never used by the pipeline writer or WebWright selection. This cannot create
or establish the quality of gold labels; callers must independently prepare
them and freeze the candidate before opening held inputs.
"""
import argparse
from collections import Counter
import json
from pathlib import Path


def load_predictions(source):
    if source.is_file():
        value=json.loads(source.read_text(encoding='utf-8'))
        if not isinstance(value,dict): raise ValueError('prediction JSON must be an object keyed by task ID')
        return value
    if not source.is_dir(): raise ValueError('prediction path does not exist')
    root=source.resolve()
    values={}
    for path in sorted(root.glob('*/workspace/execution/final_script_log.txt')):
        task_id=path.relative_to(root).parts[0]
        try:
            if not path.resolve(strict=True).is_relative_to(root) or path.is_symlink() or path.stat().st_size>5_000_000:
                raise ValueError('unsafe or oversized log')
            lines=path.read_text(encoding='utf-8').splitlines()
            line=next(x for x in reversed(lines) if x.startswith('final result: '))
            values[task_id]=json.loads(line[len('final result: '):])
        except (OSError,ValueError,StopIteration):
            values[task_id]=None
    return values


def exact(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(exact(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left)==len(right) and all(exact(a,b) for a,b in zip(left,right))
    return left == right


def score(gold, predictions):
    if not isinstance(gold,list) or not gold:
        raise ValueError('gold must be a nonempty list')
    task_ids=[x.get('task_id') for x in gold if isinstance(x,dict)]
    if len(task_ids)!=len(gold) or any(not isinstance(x,str) or not x for x in task_ids) or len(set(task_ids))!=len(task_ids):
        raise ValueError('gold task IDs must be unique nonempty strings')
    rows=[]
    for item in gold:
        if 'expected_result' not in item:
            raise ValueError('each gold case needs expected_result')
        task_id=item['task_id']
        if task_id not in predictions:
            status='missing'
        elif not isinstance(predictions[task_id],dict) or 'result' not in predictions[task_id]:
            status='invalid'
        else:
            status='pass' if exact(item['expected_result'],predictions[task_id]['result']) else 'mismatch'
        rows.append({'task_id':task_id,'status':status})
    counts=Counter(x['status'] for x in rows)
    return {'gold_count':len(gold),'pass_count':counts['pass'],'mismatch_count':counts['mismatch'],
            'missing_count':counts['missing'],'invalid_count':counts['invalid'],
            'strict_pass_rate':counts['pass']/len(gold), 'cases':rows,
            'limitations':['Exact comparison is order- and type-sensitive; semantically equivalent results may not match.',
                           'Scores depend on independent, correct, time-appropriate gold labels.',
                           'This offline audit is not part of autonomous WebWright selection.']}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('gold',type=Path,help='JSON list of {task_id, expected_result} cases')
    p.add_argument('predictions',type=Path,help='JSON mapping of task IDs to results, or held_checks directory')
    args=p.parse_args()
    print(json.dumps(score(json.loads(args.gold.read_text()),load_predictions(args.predictions)),indent=2))


if __name__=='__main__': main()
