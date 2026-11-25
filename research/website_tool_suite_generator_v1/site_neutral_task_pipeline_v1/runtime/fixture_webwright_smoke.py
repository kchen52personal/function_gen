"""Single bounded WebWright integration smoke; not a pipeline benchmark."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import time
import yaml

PROJECT = Path('/shared/hans/research/website_tool_suite_generator_v1')
RUNTIME = Path(__file__).resolve().parent
PIPELINE = PROJECT / 'pipeline_codex_v73'
sys.path[:0] = [str(RUNTIME), str(PIPELINE)]
from base_config import build_config
from codex_bounded_process import run_bounded
from tool_broker import Broker

ROOT = PROJECT / 'automated_runs/local_fixture_webwright_smoke_20261117_v1'
WORKSPACE = PROJECT / 'fixture_agent_20261117v1'
FIXTURE = PROJECT / 'local_fixtures/ecommerce_storefront_20261117'
URL = 'http://127.0.0.1:18765/'
TASK = ('On this local demonstration storefront, find the Aluminum Laptop Stand '
        'and report its displayed price. Save a screenshot as evidence and '
        'submit the reusable browser script. Do not purchase anything.')


def write(value):
    temporary = ROOT / '.summary.tmp'
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    os.replace(temporary, ROOT / 'summary.json')


def main():
    raise RuntimeError('fixture WebWright smoke disabled until host Unix socket isolation is fixed')
    ROOT.mkdir(parents=True, exist_ok=False)
    WORKSPACE.mkdir(exist_ok=False)
    for name in ('.tmp', '.cache', '.config'):
        (ROOT/name).mkdir()
    shutil.copyfile(RUNTIME/'broker_tool_model.py', WORKSPACE/'broker_tool_model.py')
    (WORKSPACE/'tool_model.yaml').write_text(yaml.safe_dump({'model': {
        'model_class': 'broker_tool_model.BrokerToolModel', 'model_name': 'qwen3.5-plus',
        'request_timeout_seconds': 180}}))
    config = build_config({'success_criteria': ['Report the product price visible on the site.'],
                           'requested_constraints': ['No purchases or real personal information.']}, ROOT)
    config['model']['model_name'] = 'qwen3.5-plus'
    config['model']['max_api_calls'] = 6
    config['agent']['step_limit'] = 6
    config['agent']['system_template'] = config['agent']['system_template'].replace('six-call cap', 'two-call cap')
    config['environment']['shell'] = str(RUNTIME/'sandbox_fixture_v1.py')
    config['environment']['env']['FIXTURE_ROOT'] = str(FIXTURE)
    (ROOT/'config.yaml').write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    state = {'status':'running', 'started_at':datetime.now(timezone.utc).isoformat(),
        'purpose':'integration smoke only; known development task, not an evaluation score',
        'task':TASK, 'model':'qwen3.5-plus', 'workspace':str(WORKSPACE),
        'agent_call_cap':6, 'inner_call_cap':2, 'wall_timeout_seconds':900,
        'limitations':['Submission is not independent proof of correctness.',
            'No generalization, held-out, multi-task, or complex-task score is measured.',
            'Each shell command runs a new fixture instance; browser state is not persistent.',
            'The local test-only sandbox and broker are experimental, not a security audit.']}
    write(state)
    env = {**os.environ, 'PYTHONPATH': f'{PIPELINE}:{PROJECT / "vendor/Webwright/src"}:{RUNTIME}',
        'PLAYWRIGHT_BROWSERS_PATH': str(PROJECT/'.playwright-browsers'),
        'FIXTURE_ROOT':str(FIXTURE), 'XDG_CONFIG_HOME':str(ROOT/'.config'),
        'XDG_CACHE_HOME':str(ROOT/'.cache'), 'TMPDIR':str(ROOT/'.tmp'), 'PYTHONDONTWRITEBYTECODE':'1'}
    command = [sys.executable, str(RUNTIME/'launch_agent_v3.py'), '--task', TASK,
        '--task-id','local_fixture_smoke', '--workspace',str(WORKSPACE),
        '--config',str(ROOT/'config.yaml'), '--start-url',URL]
    started = time.monotonic()
    try:
        with Broker(WORKSPACE, ROOT, limit=2, model='qwen3.5-plus', pathname_socket=True) as broker:
            code, stdout, stderr = run_bounded(command, cwd=PROJECT, env=env, timeout=900,
                cancel_requested=broker.budget_exhausted.is_set, cancel_reason='inner model budget exhausted')
            state['inner_calls'] = broker.count
        (ROOT/'stdout.log').write_text(stdout)
        (ROOT/'stderr.log').write_text(stderr)
        info = {}
        if (WORKSPACE/'trajectory.json').is_file():
            info = json.loads((WORKSPACE/'trajectory.json').read_text()).get('info', {})
        script = WORKSPACE/'final_script.py'
        state.update(status='finished', process_exit=code, trajectory_status=info.get('exit_status'),
            agent_calls=info.get('api_calls'), final_script_present=script.is_file() and not script.is_symlink(),
            semantic_correctness='not_independently_verified')
    except Exception as error:
        state.update(status='infrastructure_error', error_type=type(error).__name__, error=str(error)[:1000])
    state['elapsed_seconds'] = round(time.monotonic()-started, 2)
    state['finished_at'] = datetime.now(timezone.utc).isoformat()
    write(state)


if __name__ == '__main__':
    main()
