"""Test verifier sensitivity to two controlled mutations of saved development output."""
import copy
import argparse
import json
from pathlib import Path

import run_visual_experiment as fixtures
import visual_verifier as verifier

OUT = fixtures.ROOT / 'automated_runs/visual_arxiv_mutations_20261117_v1'


def main():
    global OUT
    parser = argparse.ArgumentParser()
    parser.add_argument('--name', default='visual_arxiv_mutations_20261117_v1')
    parser.add_argument('--case', choices=('wrong_title', 'wrong_date', 'both'), default='both')
    args = parser.parse_args()
    if Path(args.name).name != args.name or args.name in ('.', '..'):
        parser.error('name must be a simple directory name')
    OUT = fixtures.ROOT / 'automated_runs' / args.name
    _, site, contract, task, result, images, legacy = fixtures.saved_case(
        'arxiv', 'arxiv_8dev_webwright_only_20261116_v1', 'task_003', 'run_001')
    OUT.mkdir(parents=True, exist_ok=False)
    wrong_title = copy.deepcopy(result)
    wrong_title['result']['items'][0]['title'] = 'A completely unrelated invented title'
    wrong_date = copy.deepcopy(result)
    wrong_date['result']['items'][0]['date'] = '1 January, 1990'
    cases = [('wrong_title', wrong_title), ('wrong_date', wrong_date)]
    if args.case != 'both':
        cases = [(name, answer) for name, answer in cases if name == args.case]
    records = []
    for name, answer in cases:
        report = verifier.judge(site, contract, task, answer, images, OUT / name,
                                legacy_marker=legacy, model='qwen3.5-flash', timeout_seconds=90)
        records.append({'case': name, 'expected': 'fail', 'status': report['status'],
                        'error_type': report.get('error_type')})
        (OUT / 'summary.json').write_text(json.dumps({'cases': records, 'held_opened': False}, indent=2) + '\n')
        print(name, report['status'], report.get('error_type'), flush=True)
        if report.get('error_type'):
            break


if __name__ == '__main__':
    main()
