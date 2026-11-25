#!/usr/bin/env python3
"""One-candidate Amazon pilot: generate, execute twice, independently verify.

This is a bounded pilot for search_products, not a universal ten-tool loop.
Every invocation uses a new output directory and fails closed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import yaml

ROOT = Path('/shared/hans/research/website_tool_suite_generator_v1')
PIPELINE = ROOT / 'pipeline'
SHARED = Path('/shared/hans')


def inside_shared(path: Path) -> Path:
    path = path.resolve()
    if path != SHARED and SHARED not in path.parents:
        raise ValueError('output must be inside /shared/hans')
    return path


def run(command: list[str], output: Path, env: dict[str, str], timeout: int) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    try:
        result = subprocess.run(command, cwd=SHARED, env=env, capture_output=True,
                                text=True, timeout=timeout, check=False)
        code, stdout, stderr = result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        code = 124
        stdout = exc.stdout or ''
        stderr = exc.stderr or ''
        if isinstance(stdout, bytes):
            stdout = stdout.decode('utf-8', 'replace')
        if isinstance(stderr, bytes):
            stderr = stderr.decode('utf-8', 'replace')
        stderr += f'\nTimed out after {timeout}s\n'
    (output / 'stdout.txt').write_text(stdout, encoding='utf-8')
    (output / 'stderr.txt').write_text(stderr, encoding='utf-8')
    return {'exit_code': code, 'stdout': str(output / 'stdout.txt'),
            'stderr': str(output / 'stderr.txt')}


def eligible_for_acceptance(runs: dict, input_errors: list[str],
                            verifier_exit: int, report: dict) -> bool:
    """Never count a tool if either execution or the independent oracle failed."""
    return (
        set(runs) == {'default', 'held'}
        and all(runs[label].get('exit_code') == 0 for label in ('default', 'held'))
        and not input_errors
        and verifier_exit == 0
        and report.get('status') == 'pass'
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = inside_shared(args.output)
    output.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ)
    env['PYTHONPATH'] = f'{ROOT / "vendor/Webwright/src"}:{PIPELINE}'
    env['PLAYWRIGHT_BROWSERS_PATH'] = str(ROOT / '.playwright-browsers')
    env['XDG_CONFIG_HOME'] = str(ROOT / '.webwright-config')

    summary = {'site': 'amazon_sg', 'target': 10, 'pilot_candidate': 'search_products',
               'scope': 'single known candidate pilot, not an autonomous ten-tool suite',
               'started_at': datetime.now(timezone.utc).isoformat(),
               'status': 'running', 'attempts': [], 'accepted': [], 'verified_count': 0}
    def save() -> None:
        (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    save()
    base = yaml.safe_load((PIPELINE / 'webwright_yibu_qwen35plus_search_products_v2.yaml').read_text())
    base['model']['api_log_path'] = str(output / 'api_call.json')
    base['model']['max_api_calls'] = 3
    base['agent']['step_limit'] = 3
    base['agent']['system_template'] = base['agent']['system_template'].replace(
        'Your one\ncommand writes final_script.py. Never read local files or access secrets.',
        'Write final_script.py. You may run your generated script to inspect failures and '
        'repair it within this workspace. Never read unrelated local files or access secrets.')
    base['agent']['instance_template'] = (
        'Task: {{ task }}\nTask ID: {{ task_id }}\nStart URL: {{ start_url }}\n'
        'Workspace: {{ workspace_dir }}\nWrite the tool. Run it if time permits and repair errors. '
        'The external runner will independently test and verify it after you finish.')
    config = output / 'webwright_config.yaml'
    config.write_text(yaml.safe_dump(base, sort_keys=False), encoding='utf-8')
    generated = output / 'generated'
    attempt = {'candidate': 'search_products', 'status': 'generating'}
    summary['attempts'].append(attempt)
    save()
    gen = run([sys.executable, str(PIPELINE / 'run_webwright_pilot.py'),
               '--task', 'Generate an Amazon Singapore product search tool.',
               '--task-id', 'amazon_verified_pilot_search_products',
               '--start-url', 'https://www.amazon.sg/', '--output-dir', str(generated),
               '--config', str(config)], output / 'generation_process', env, 600)
    attempt['generation'] = gen
    script = generated / 'final_script.py'
    if gen['exit_code'] != 0 or not script.is_file():
        attempt['status'] = 'generation_failed'
    else:
        attempt['script_sha256'] = hashlib.sha256(script.read_bytes()).hexdigest()
        attempt['runs'] = {}
        for label, query, limit in [('default', 'wireless mouse', '8'), ('held', 'usb keyboard', '6')]:
            case = output / 'cases' / label
            result = run([sys.executable, str(script), '--query', query, '--limit', limit,
                          '--output-dir', str(case)], output / 'case_processes' / label,
                         env, 150)
            attempt['runs'][label] = result
            save()
        input_errors = []
        for label, query, limit in [('default', 'wireless mouse', 8), ('held', 'usb keyboard', 6)]:
            try:
                lines = (output / 'cases' / label / 'final_script_log.txt').read_text(encoding='utf-8').splitlines()
                result_line = next(line for line in reversed(lines) if line.startswith('final result: '))
                result = json.loads(result_line[len('final result: '):])
                if result.get('query') != query or result.get('limit') != limit:
                    input_errors.append(f'{label}: output does not match supplied query and limit')
            except (OSError, ValueError, StopIteration, AttributeError):
                input_errors.append(f'{label}: missing or invalid result log')
        if hashlib.sha256(script.read_bytes()).hexdigest() != attempt['script_sha256']:
            input_errors.append('generated script changed while executing')
        attempt['input_errors'] = input_errors
        if any(item['exit_code'] != 0 for item in attempt['runs'].values()) or input_errors:
            attempt['status'] = 'execution_failed'
        else:
            verification_file = output / 'verification.json'
            check = run([sys.executable, str(PIPELINE / 'verify_amazon_product_search.py'),
                         '--default-dir', str(output / 'cases/default'),
                         '--held-dir', str(output / 'cases/held'), '--output', str(verification_file)],
                        output / 'verifier_process', env, 240)
            attempt['verification'] = check
            try:
                report = json.loads(verification_file.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                report = {}
            if eligible_for_acceptance(attempt['runs'], input_errors, check['exit_code'], report):
                attempt['status'] = 'verified'
                summary['accepted'].append({'name': 'search_products', 'script': str(script),
                                            'script_sha256': attempt['script_sha256'],
                                            'verification': str(verification_file)})
            else:
                attempt['status'] = 'verification_failed'
    summary['verified_count'] = len(summary['accepted'])
    summary['status'] = 'complete' if summary['verified_count'] else 'complete_no_verified_tools'
    summary['finished_at'] = datetime.now(timezone.utc).isoformat()
    save()
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
