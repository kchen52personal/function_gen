import json
import os
from pathlib import Path

import pytest

import development

def test_smoke_test_gets_only_current_development_case(tmp_path, monkeypatch):
    def base(task, root, model):
        return {'model':{}, 'agent':{'system_template':'keep six-call cap'},
                'environment':{'env':{}}}
    monkeypatch.setattr(development, 'BASE_CONFIG', base)
    task = {'task_id':'dev_1','parameters':{'query':'current input'},'intent':'search'}
    contract = {'returns':{'items':'list[object]'}}
    config = development.build_task_config(task, tmp_path, 'qwen3.8-flash', contract)
    env = config['environment']['env']
    assert json.loads(env['CODEX_VISIBLE_CASE_JSON']) == task['parameters']
    assert json.loads(env['CODEX_RETURN_SCHEMA_JSON']) == contract['returns']
    assert 'never include output_dir' in config['agent']['system_template']
    assert 'automatically runs a disclosed development-case smoke test' in config['agent']['system_template']
    assert config['agent']['require_self_reflection_success'] is True

def test_state_changing_prompt_does_not_claim_automatic_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(development, 'BASE_CONFIG', lambda *args:
        {'model':{}, 'agent':{'system_template':'six-call cap'}, 'environment':{'env':{}}})
    task = {'task_id':'dev', 'parameters':{'request':'create'}, 'intent':'create'}
    config = development.build_task_config(task, tmp_path, 'model', {'returns':{'status':'str'}},
                                           state_changing=True)
    prompt = config['agent']['system_template']
    assert 'without executing it' in prompt
    assert 'automatically runs a disclosed' not in prompt

@pytest.mark.parametrize(('host','params','returns'), [
    ('https://catalog.example/', {'query':'visible book'}, {'items':'list[object]'}),
    ('https://weather.example/', {'city':'sample town'}, {'temperature':'number'}),
])
def test_real_shared_configuration_handles_different_sites_and_shapes(tmp_path, host, params, returns):
    task = {'task_id':'dev_1', 'parameters':params, 'intent':'Perform the requested operation'}
    contract = {'goal':'Read public information', 'boundary':'Read-only',
                'success_contract':'Return the requested values', 'returns':returns}
    config = development.build_task_config(task, tmp_path, 'qwen3.8-flash', contract)
    assert json.loads(config['environment']['env']['CODEX_VISIBLE_CASE_JSON']) == params
    assert json.loads(config['environment']['env']['CODEX_RETURN_SCHEMA_JSON']) == returns
    shell = Path(config['environment']['shell'])
    assert shell.parent == Path(development.__file__).parent/'runtime'
    assert shell.is_file() and os.access(shell, os.X_OK)
    assert str(Path(development.runner.__file__).parent).endswith('site_neutral_task_pipeline_v1/runtime')

@pytest.mark.parametrize(('returns','expected'), [
    ({'papers':'list[object]'}, '"result": {"papers": []}'),
    ({'temperature':'number'}, '"result": {"temperature": 0}'),
])
def test_development_prompt_uses_actual_contract_not_unrelated_example(tmp_path, monkeypatch, returns, expected):
    monkeypatch.setattr(development, 'BASE_CONFIG', lambda *args:
        {'model':{}, 'agent':{'system_template':'six-call cap'}, 'environment':{'env':{}}})
    task = {'task_id':'dev', 'parameters':{'query':'development input'}, 'intent':'read'}
    config = development.build_task_config(task, tmp_path, 'model', {'returns':returns})
    prompt = config['agent']['system_template']
    assert expected in prompt
    assert 'Structural illustration only' in prompt
    assert '"items":"list[object]"' not in prompt
    assert 'hidden test value' not in prompt
