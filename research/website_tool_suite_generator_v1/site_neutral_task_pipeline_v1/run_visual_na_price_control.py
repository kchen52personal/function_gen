"""Judge actual N/A-price output using saved local development screenshots."""
import json
import re
from pathlib import Path
import visual_verifier as verifier

ROOT = Path('/shared/hans/research/website_tool_suite_generator_v1')
SOURCE = ROOT / 'ct_fixture_sort_20261117v3/final_runs/run_003'
OUT = ROOT / 'automated_runs/visual_sort_na_20261117_v1'


def main():
    log = (SOURCE / 'final_script_log.txt').read_text()
    items = [{'name': name, 'price': price} for name, price in
             re.findall(r'^\d+\. (.+?) - (.+)$', log, flags=re.MULTILINE)]
    if len(items) != 4 or any(item['price'] != 'N/A' for item in items):
        raise ValueError('unexpected saved output')
    contract = {'goal': 'Show the Gear category sorted by price low to high and report visible names and prices.',
                'returns': {'items': 'list[object]'}}
    task = {'task_id': 'sort_na_control', 'intent': contract['goal'], 'parameters': {'category': 'Gear'}}
    result = {'parameters': task['parameters'], 'result': {'items': items}, 'source_url': 'http://127.0.0.1:18765/'}
    report = verifier.judge('http://127.0.0.1:18765/', contract, task, result,
        [SOURCE / 'screenshots/final_execution_3_final_state.png'], OUT,
        model='qwen3.5-flash', timeout_seconds=90)
    print(json.dumps({'expected': 'fail', 'status': report['status'], 'error_type': report.get('error_type')}))


if __name__ == '__main__':
    main()
