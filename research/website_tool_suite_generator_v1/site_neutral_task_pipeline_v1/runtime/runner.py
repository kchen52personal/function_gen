#!/usr/bin/env python3
"""Bounded site-neutral WebWright development runner."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
from uuid import uuid4
import yaml

PROJECT = Path('/shared/hans/research/website_tool_suite_generator_v1')
COMMON = Path(__file__).resolve().parent
EXPERIMENT = COMMON
PIPELINE = PROJECT / 'pipeline_codex_v73'
sys.path[:0] = [str(COMMON), str(PIPELINE)]
from codex_bounded_process import run_bounded
from base_config import build_config
from tool_broker import Broker
from access_guard import repeated_access_errors

TASKS = None  # supplied by the site-neutral pipeline
OUT = None  # supplied by the site-neutral pipeline
URL = None  # supplied by the site-neutral pipeline
MODELS = ('qwen3.5-plus', 'gpt-5.4')
TASK_LIMIT = 3
AGENT_CAP = 20
INNER_CAP = 6
ATTEMPT_TIMEOUT = 900
WALL_TIMEOUT = 7200


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def config_for(task: dict, root: Path, model: str) -> dict:
    # No generated success criteria or hidden constraints: the intent is the
    # only task specification. Human review decides whether it was satisfied.
    value = {'success_criteria': ['Satisfy the user request using visible evidence; do not invent requirements.'],
             'requested_constraints': []}
    config = build_config(value, root)
    config['model']['model_name'] = model
    config['model']['max_api_calls'] = AGENT_CAP
    config['agent']['step_limit'] = AGENT_CAP
    config['agent']['summary_every_n_steps'] = 8
    config['environment']['output_truncation_chars'] = 4000
    return config


def select_tasks(development):
    if len(development) < TASK_LIMIT:
        raise ValueError('development set is smaller than comparison sample')
    return [development[0], development[len(development) // 2], development[-1]]


def main() -> None:
    development = json.loads(TASKS.read_text(encoding='utf-8'))['tasks']
    tasks = select_tasks(development)
    OUT.mkdir(parents=True, exist_ok=False)
    for name in ('.config', '.cache', '.tmp'):
        (OUT / name).mkdir()
    state = {'status': 'running', 'started_at': datetime.now(timezone.utc).isoformat(),
             'site': URL, 'selection': 'first, middle and last task in development manifest',
             'tasks': tasks, 'models': list(MODELS), 'agent_call_cap_per_attempt': AGENT_CAP,
             'inner_call_cap_per_attempt': INNER_CAP, 'per_attempt_timeout_seconds': ATTEMPT_TIMEOUT,
             'wall_timeout_seconds': WALL_TIMEOUT, 'attempts': [],
             'review_policy': 'Submission is not independent verification; later assessment depends on the caller.',
             'limitations': ['A three-task sample is exploratory, not a model ranking.',
                 'Site content and time may vary between sequential runs.',
                 'Model self-reflection is not independent verification.',
                 'Sandbox does not enforce network allowlist or browser read-only behavior.']}
    write_json(OUT / 'summary.json', state)
    env = {**os.environ, 'PYTHONPATH': f'{PIPELINE}:{PROJECT / "vendor/Webwright/src"}:{COMMON}',
           'PLAYWRIGHT_BROWSERS_PATH': str(PROJECT / '.playwright-browsers'),
           'XDG_CONFIG_HOME': str(OUT / '.config'), 'XDG_CACHE_HOME': str(OUT / '.cache'),
           'TMPDIR': str(OUT / '.tmp'), 'PYTHONDONTWRITEBYTECODE': '1'}
    deadline = time.monotonic() + WALL_TIMEOUT
    for task in tasks:
        for model in MODELS:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                state.update({'status': 'bounded_incomplete', 'finished_at': datetime.now(timezone.utc).isoformat()})
                state.pop('current', None)
                write_json(OUT / 'summary.json', state)
                return
            root = OUT / task['task_id'] / model
            root.mkdir(parents=True, exist_ok=False)
            workspace = root / 'agent'
            workspace.mkdir()
            shutil.copyfile(COMMON / 'broker_tool_model.py', workspace / 'broker_tool_model.py')
            (workspace / 'tool_model.yaml').write_text(yaml.safe_dump({'model': {
                'model_class': 'broker_tool_model.BrokerToolModel', 'model_name': model,
                'request_timeout_seconds': 180}}), encoding='utf-8')
            write_json(root / 'task.json', task)
            config = config_for(task, root, model)
            (root / 'config.yaml').write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding='utf-8')
            attempt_started = time.monotonic()
            attempt_started_at = datetime.now(timezone.utc).isoformat()
            state['current'] = {'task_id': task['task_id'], 'model': model, 'started_at': attempt_started_at}
            write_json(OUT / 'summary.json', state)
            cmd = [sys.executable, str(COMMON / 'launch_agent_v3.py'), '--task', task['intent'],
                   '--task-id', task['task_id'], '--workspace', str(workspace),
                   '--config', str(root / 'config.yaml'), '--start-url', URL]
            with Broker(workspace, root, limit=INNER_CAP, model=model) as broker:
                access = {'detected':False,'distinct_failed_steps':0}
                last_access_scan = 0.0
                def cancel_requested():
                    nonlocal access, last_access_scan
                    now=time.monotonic()
                    if now-last_access_scan >= 1:
                        access=repeated_access_errors(workspace,URL)
                        last_access_scan=now
                    return broker.budget_exhausted.is_set() or access['detected']
                code, stdout, stderr = run_bounded(
                    cmd, cwd=PROJECT, env=env, timeout=min(ATTEMPT_TIMEOUT, remaining),
                    cancel_requested=cancel_requested,
                    cancel_reason='process stopped: inner-tool budget or repeated site-access failures',
                )
                inner_calls = broker.count
                inner_budget_exhausted = broker.budget_exhausted.is_set()
            (root / 'stdout.log').write_text(stdout, encoding='utf-8')
            (root / 'stderr.log').write_text(stderr, encoding='utf-8')
            trajectory = workspace / 'trajectory.json'
            info = {}
            if trajectory.is_file():
                try:
                    info = json.loads(trajectory.read_text(encoding='utf-8')).get('info', {})
                except (OSError, ValueError):
                    pass
            script = workspace / 'final_script.py'
            # Do not follow model-produced symlinks to external files.
            script_ok = script.is_file() and not script.is_symlink()
            result = {'task_id': task['task_id'], 'model': model, 'process_exit': code,
                      'trajectory_status': info.get('exit_status'), 'agent_calls': info.get('api_calls'),
                      'inner_tool_calls': inner_calls, 'final_script_present': script_ok,
                      'inner_tool_budget_exhausted': inner_budget_exhausted,
                      'site_access_failure_signal': access['detected'],
                      'site_access_failure_steps': access['distinct_failed_steps'],
                      'started_at': attempt_started_at,
                      'finished_at': datetime.now(timezone.utc).isoformat(),
                      'elapsed_seconds': round(time.monotonic() - attempt_started, 3),
                      'script_sha256': hashlib.sha256(script.read_bytes()).hexdigest() if script_ok else None,
                      'independent_human_review': False, 'attempt_dir': str(root)}
            write_json(root / 'attempt_summary.json', result)
            state['attempts'].append(result)
            write_json(OUT / 'summary.json', state)
            if access['detected'] and not (result['trajectory_status']=='Submitted' and code==0):
                state.update(status='stopped_repeated_site_access_failures',
                             finished_at=datetime.now(timezone.utc).isoformat(),
                             stop_reason='Repeated command logs showed target-host access failures; this is not a model-correctness verdict.')
                state.pop('current', None)
                write_json(OUT / 'summary.json', state)
                return
    state.update({'status': 'attempts_complete', 'finished_at': datetime.now(timezone.utc).isoformat()})
    state.pop('current', None)
    write_json(OUT / 'summary.json', state)


if __name__ == '__main__':
    main()
