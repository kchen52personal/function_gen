"""Shared site-neutral WebWright configuration."""
from pathlib import Path
import json
import yaml
from codex_prompt_escape import escape_jinja_literal

EXPERIMENT = Path(__file__).resolve().parent
PROJECT = Path('/shared/hans/research/website_tool_suite_generator_v1')
BASE = PROJECT / 'vendor/Webwright/src/cuawright/webwright/config/base.yaml'

def build_config(task: dict, root: Path) -> dict:
    base = yaml.safe_load(BASE.read_text(encoding='utf-8'))
    cfg = {key: base[key] for key in ('model', 'environment', 'agent')}
    cfg['model'].update({'model_class': 'webwright_yibu_model.YibuWebWrightModel',
        'model_name': 'qwen3.5-plus', 'openrouter_endpoint': 'https://yibuapi.com/v1/chat/completions',
        'provider_require_parameters': False, 'api_key_file': '/shared/hans/.secrets/website_tool_api_key',
        'api_log_path': str(root / 'api_call.json'), 'spend_ledger_path': '/shared/hans/webarena/spend_ledger.jsonl',
        'max_api_calls': 15, 'max_output_tokens': 6144, 'request_timeout_seconds': 180})
    cfg['environment'].update({'environment_class': 'webwright_safe_environment.SafeToolWorkspaceEnvironment',
        'command_timeout_seconds': 120, 'shell': str(EXPERIMENT / 'sandbox_v3.py'), 'browser_mode': 'local',
        'output_truncation_chars': 24000})
    cfg['environment']['env']['PLAYWRIGHT_BROWSERS_PATH'] = str(PROJECT / '.playwright-browsers')
    cfg['agent'].update({'step_limit': 15, 'require_self_reflection_success': True, 'summary_every_n_steps': 0})
    # The built-in tool CLIs accept explicit model-config; point them to the
    # confined proxy model rather than exposing credentials to the agent.
    for key in ('system_template', 'instance_template'):
        cfg['agent'][key] = cfg['agent'][key].replace(
            '--workspace-dir "{{ workspace_dir }}"',
            '--workspace-dir "{{ workspace_dir }}" --model-config "{{ workspace_dir }}/tool_model.yaml"')
    extra = ('\n\nAdditional required constraints: this is a public read-only task. '
             'Never log in, purchase, add to a cart, or change account/site state. '
             'Use the tool_model.yaml in the workspace for every image_qa and '
             'self_reflection invocation. Inner-tool requests have a six-call cap; '
             'the model and broker never receive access to credentials in generated code. '
             'Keep screenshots to at most four per final run. Use visible evidence and '
             'report unproven constraints honestly.\nTask criteria: '
             + json.dumps(task['success_criteria'], ensure_ascii=False)
             + '\nExplicit constraints: ' + json.dumps(task['requested_constraints'], ensure_ascii=False))
    cfg['agent']['system_template'] += escape_jinja_literal(extra)
    return cfg
