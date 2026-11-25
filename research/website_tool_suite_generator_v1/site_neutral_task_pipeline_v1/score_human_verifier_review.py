"""Score verifier predictions against human labels for the same narrow criterion.

This is offline analysis, not a pipeline gate. Human labels of a final screen
must not be represented as proof of the full task, action history or URL.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path


def compare(cases):
    if not isinstance(cases, list) or not cases:
        raise ValueError('cases must be a nonempty list')
    counts = Counter()
    ids = set()
    rows = []
    for item in cases:
        if not isinstance(item, dict):
            raise ValueError('each case must be an object')
        case_id = item.get('case_id')
        human, prediction = item.get('human_label'), item.get('verifier_label')
        if not isinstance(case_id, str) or not case_id or case_id in ids:
            raise ValueError('case IDs must be unique nonempty strings')
        if item.get('criterion') != 'requested_note_visible_on_final_screen':
            raise ValueError('criterion must match the reviewed observation')
        if human not in {'yes', 'no', 'unsure'} or prediction not in {'pass', 'fail', 'inconclusive', None}:
            raise ValueError('invalid human or verifier label')
        ids.add(case_id)
        if prediction is None:
            classification = 'prediction_missing'
        elif human == 'unsure':
            classification = 'human_unsure'
        elif prediction == 'inconclusive':
            classification = 'verifier_inconclusive'
        elif human == 'yes':
            classification = 'agreement' if prediction == 'pass' else 'false_rejection'
        else:
            classification = 'false_acceptance' if prediction == 'pass' else 'agreement'
        counts[classification] += 1
        rows.append({'case_id': case_id, 'classification': classification})
    return {'total': len(cases), 'counts': dict(counts), 'cases': rows,
            'criterion': 'requested_note_visible_on_final_screen',
            'limitations': ['A visible final state does not prove correct actions, URL, returned data, or lack of side effects.',
                            'Missing/uncertain labels are not scored as correct.',
                            'A small selected sample is not a reliability estimate.',
                            'This offline report cannot accept a pipeline result.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cases_json', type=Path)
    args = parser.parse_args()
    print(json.dumps(compare(json.loads(args.cases_json.read_text(encoding='utf-8'))), indent=2))


if __name__ == '__main__':
    main()
