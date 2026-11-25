"""Run bounded, labeled verifier controls; no held-out tasks are read."""
import json
from pathlib import Path

import run_visual_experiment as fixtures
import visual_verifier as verifier

OUT = fixtures.ROOT / 'automated_runs/visual_verifier_controls_20261117_v2'


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    images = OUT / 'images'
    images.mkdir()
    results = []
    consecutive_errors = 0
    for name, expected, image, contract, task, answer in fixtures.control_cases(images):
        if consecutive_errors >= 2:
            results.append({'case': name, 'expected': expected, 'status': 'skipped_after_api_failures'})
            continue
        report = verifier.judge('https://example.org/', contract, task, answer, [image], OUT / name,
                                model='qwen3.5-flash', timeout_seconds=90)
        consecutive_errors = consecutive_errors + 1 if report.get('error_type') else 0
        results.append({'case': name, 'expected': expected, 'status': report['status'],
                        'error_type': report.get('error_type')})
        (OUT / 'summary.json').write_text(json.dumps({'cases': results, 'held_opened': False}, indent=2) + '\n')
        print(name, report['status'], report.get('error_type'), flush=True)
    (OUT / 'summary.json').write_text(json.dumps({'cases': results, 'held_opened': False}, indent=2) + '\n')


if __name__ == '__main__':
    main()
