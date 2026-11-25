#!/usr/bin/env python3
"""Bounded WebWright repair from a previous script and disclosed failure feedback.

This is a generic orchestration stage: it does not construct site-specific tool code,
choose held-out tests, or treat model self-reflection as independent verification.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from urllib.parse import urlparse
import yaml

PROJECT = Path('/shared/hans/research/website_tool_suite_generator_v1')
COMMON = Path('/shared/hans/research/amazon_search_trajectory_experiment_v1')
PIPELINE = PROJECT / 'pipeline_codex_v73'
sys.path[:0] = [str(COMMON), str(PIPELINE)]
from codex_bounded_process import run_bounded
from run_pilot_v3 import build_config
from tool_broker import Broker

MODEL = 'qwen3.5-plus'
AGENT_STEPS = 100
AGENT_CALLS = 110
INNER_CALLS = 6
WALL_SECONDS = 1800


def checked_input(path: Path) -> Path:
    path = path.absolute()
    if path.is_symlink() or not path.is_file():
        raise ValueError('input must be a regular, non-symlink file')
    resolved = path.resolve(strict=True)
    if Path('/shared/hans') not in resolved.parents:
        raise ValueError('input must be inside workspace')
    if any('held_out' in part or 'holdout' in part for part in resolved.parts):
        raise ValueError('held-out artifacts cannot be repair inputs')
    return resolved


def load_feedback(path: Path) -> dict:
    value = json.loads(checked_input(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict) or set(value) != {'task', 'observations'}:
        raise ValueError('feedback must contain task and observations only')
    if not isinstance(value['task'], str) or not value['task'].strip() or len(value['task']) > 12000:
        raise ValueError('invalid repair task')
    if not isinstance(value['observations'], list) or not all(isinstance(x, str) and len(x) <= 3000 for x in value['observations']):
        raise ValueError('invalid observations')
    serialized = json.dumps(value, ensure_ascii=False).casefold()
    if 'held_out/' in serialized or 'fresh_holdout' in serialized:
        raise ValueError('feedback references held-out artifacts')
    return value


def build_task(feedback: dict) -> str:
    return ('Repair the existing generalized browser program using only the disclosed development feedback below. '
            'The unchanged original is in previous_function.py. Inspect the live site yourself, revise and execute '
            'the code, and leave the complete reusable program in final_script.py. Do not invent records or use '
            'anything outside the workspace. Keep the originally requested CLI interface. '
            'For WebWright replay and self-reflection, final_script.py must also be executable without arguments '
            'using a disclosed development example as its default, with a final_runs run log and bounded screenshots; '
            'when CLI arguments are supplied it must follow the specified output contract. '
            'Do not claim success if the live development checks fail.\n\nTASK:\n' + feedback['task'] +
            '\n\nDISCLOSED DEVELOPMENT OBSERVATIONS:\n' + '\n'.join('- ' + x for x in feedback['observations']))


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def build_repair_config(root: Path) -> dict:
    cfg = build_config({'success_criteria': ['Repair the reusable function and verify the disclosed development examples on the live site.'],
                        'requested_constraints': ['Read-only browsing; preserve the requested CLI and output contract.']}, root)
    cfg['model'].update({'model_class': 'webwright_yibu_retry_model.RetryYibuWebWrightModel',
                         'model_name': MODEL, 'max_api_calls': AGENT_CALLS})
    cfg['agent'].update({'step_limit': AGENT_STEPS, 'summary_every_n_steps': 20})
    cfg['environment']['output_truncation_chars'] = 4000
    return cfg


def run(candidate_path: Path, feedback_path: Path, out: Path, start_url: str) -> None:
    candidate = checked_input(candidate_path)
    feedback = load_feedback(feedback_path)
    url = urlparse(start_url)
    if url.scheme != 'https' or not url.hostname:
        raise ValueError('start URL must be HTTPS')
    out = out.absolute()
    if out.is_symlink() or PROJECT not in out.resolve().parents:
        raise ValueError('output must be inside the project')
    source = candidate.read_bytes()
    if len(source) > 200000:
        raise ValueError('candidate is too large')
    out.mkdir(parents=True, exist_ok=False)
    workspace = out / 'agent'
    workspace.mkdir()
    for name in ('.config', '.cache', '.tmp'):
        (out / name).mkdir()
    (workspace / 'previous_function.py').write_bytes(source)
    write_json(workspace / 'repair_feedback.json', feedback)
    shutil.copyfile(COMMON / 'broker_tool_model.py', workspace / 'broker_tool_model.py')
    (workspace / 'tool_model.yaml').write_text(yaml.safe_dump({'model': {
        'model_class': 'broker_tool_model.BrokerToolModel', 'model_name': MODEL,
        'request_timeout_seconds': 180}}), encoding='utf-8')
    cfg = build_repair_config(out)
    (out / 'config.yaml').write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding='utf-8')
    state = {'status': 'running', 'started_at': datetime.now(timezone.utc).isoformat(),
             'source_path': str(candidate), 'source_sha256': hashlib.sha256(source).hexdigest(),
             'feedback_path': str(checked_input(feedback_path)), 'start_url': start_url, 'model': MODEL,
             'agent_step_cap': AGENT_STEPS, 'agent_request_cap': AGENT_CALLS,
             'inner_request_cap': INNER_CALLS, 'wall_timeout_seconds': WALL_SECONDS,
             'held_out_provided': False, 'self_reflection': 'not_independent_verification',
             'limitations': ['No browser network allowlist or hard read-only enforcement.',
                             'Generated artifact requires separate development and held-out validation.']}
    write_json(out / 'summary.json', state)
    env = {**os.environ, 'PYTHONPATH': f'{PIPELINE}:{PROJECT / "vendor/Webwright/src"}:{COMMON}',
           'PLAYWRIGHT_BROWSERS_PATH': str(PROJECT / '.playwright-browsers'),
           'XDG_CONFIG_HOME': str(out / '.config'), 'XDG_CACHE_HOME': str(out / '.cache'),
           'TMPDIR': str(out / '.tmp'), 'PYTHONDONTWRITEBYTECODE': '1'}
    command = [sys.executable, str(COMMON / 'launch_agent_v3.py'), '--task', build_task(feedback),
               '--task-id', 'feedback_repair', '--workspace', str(workspace),
               '--config', str(out / 'config.yaml'), '--start-url', start_url]
    with Broker(workspace, out, limit=INNER_CALLS, model=MODEL) as broker:
        code, stdout, stderr = run_bounded(command, cwd=PROJECT, env=env, timeout=WALL_SECONDS)
        state['inner_tool_calls'] = broker.count
    (out / 'stdout.log').write_text(stdout, encoding='utf-8')
    (out / 'stderr.log').write_text(stderr, encoding='utf-8')
    info = {}
    trajectory = workspace / 'trajectory.json'
    if trajectory.is_file() and not trajectory.is_symlink():
        try:
            info = json.loads(trajectory.read_text(encoding='utf-8')).get('info', {})
        except (OSError, ValueError):
            pass
    script = workspace / 'final_script.py'
    valid_script = script.is_file() and not script.is_symlink()
    state.update({'status': 'finished', 'finished_at': datetime.now(timezone.utc).isoformat(),
                  'process_exit': code, 'trajectory_status': info.get('exit_status'),
                  'agent_calls': info.get('api_calls'), 'final_script_present': valid_script,
                  'candidate_sha256': hashlib.sha256(script.read_bytes()).hexdigest() if valid_script else None})
    write_json(out / 'summary.json', state)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--feedback', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--start-url', required=True)
    args = parser.parse_args()
    run(args.candidate, args.feedback, args.output, args.start_url)


if __name__ == '__main__':
    main()
