from uniform_reference_agent import visible_reference_assessment, visible_reference_disagreement, visible_value_previews
from run_verified_site import evolvable_failure
import run_verified_site as pipeline
import uniform_reference_agent as reference_agent


def test_visible_advisory_reports_field_names_not_reference_answers() -> None:
    url = 'https://example.org/'
    candidate = {'returns': {'title': 'str', 'url': 'str'}, 'goal': 'Find a title and URL'}
    case = {'choice': 'first'}
    tool = {'parameters': case, 'result': {'title': 'tool value', 'url': 'https://example.org/x'}, 'source_url': url}
    reference = {'parameters': case, 'result': {'title': 'reference secret', 'url': 'https://example.org/x'}, 'source_url': url}
    grounded = {'cases': {'default': {'status': 'values_found'}}}
    result = visible_reference_disagreement(url, candidate, case, tool, reference, grounded)
    assert result == ['title']
    assert 'reference secret' not in str(result)
    assert not visible_reference_disagreement(url, candidate, case, tool, reference, {})
    malformed = {**reference, 'parameters': {'choice': 'wrong'}}
    assert not visible_reference_disagreement(url, candidate, case, tool, malformed, grounded)
    assert visible_reference_assessment(url, candidate, case, tool, reference, grounded)['status'] == 'visible_reference_mismatch'
    assert visible_reference_assessment(url, candidate, case, tool, reference, {})['status'] == 'visible_reference_unusable'
    assert visible_reference_assessment(url, candidate, case, tool, tool, grounded)['status'] == 'visible_reference_agreement'


def test_only_visible_reference_failure_is_evolvable() -> None:
    assert evolvable_failure({'status': 'visible_reference_mismatch', 'repair_context': 'title'})
    assert not evolvable_failure({'status': 'visible_reference_mismatch'})
    assert not evolvable_failure({'status': 'verification_failed', 'repair_context': 'held'})


def test_unusable_reference_keeps_bounded_default_grounding_diagnostics() -> None:
    url = 'https://example.org/'
    candidate = {'returns': {'title': 'str'}, 'goal': 'Find a title'}
    case = {'choice': 'public'}
    value = {'parameters': case, 'result': {'title': 'private answer value'}, 'source_url': url}
    grounded = {'cases': {
        'default': {'status': 'missing_values', 'source_url': 'https://example.org/search',
                    'missing_value_paths': ['$.title'] * 30},
        'held': {'status': 'error', 'reason': 'SECRET_HELD_VALUE'},
    }}
    result = visible_reference_assessment(url, candidate, case, value, value, grounded)
    assert result['status'] == 'visible_reference_unusable'
    assert result['counts_as_verified'] is False
    assert result['grounding_status'] == 'missing_values'
    assert result['grounding_source_url'] == 'https://example.org/search'
    assert result['missing_value_paths'] == ['$.title'] * 20
    assert 'private answer value' not in str(result)
    assert 'SECRET_HELD_VALUE' not in str(result)


def test_reference_prompt_requires_actual_extraction_source() -> None:
    prompt = reference_agent.prompt('https://example.org/', {'goal': 'search'},
                                    {'default': {'query': 'public'}})
    assert 'from which the result values were actually extracted' in prompt
    assert 'not the initial homepage' in prompt
    assert 'not a substitute for a requested list' in prompt


def test_visible_reference_rejects_self_modified_script(tmp_path, monkeypatch):
    monkeypatch.setattr(reference_agent, 'select_checkpoint', lambda root, workspace: None)
    monkeypatch.setattr(reference_agent, 'checked_file', lambda workspace, path: path)
    monkeypatch.setattr(reference_agent, 'ensure_screenshots', lambda workspace, path: [])
    monkeypatch.setattr(reference_agent, 'read_result', lambda path: {'parameters':{'query':'visible'},
        'result':{'title':'record'}, 'source_url':'https://example.org/'})
    async def no_grounding(*args):
        raise AssertionError('changed reference must be rejected before grounding')
    monkeypatch.setattr(reference_agent, 'grounding_check', no_grounding)
    def fake_run(command, **kwargs):
        if '--task' in command:
            workspace = __import__('pathlib').Path(command[command.index('--output-dir') + 1])
            workspace.mkdir(parents=True)
            (workspace/'final_script.py').write_text('# original')
        else:
            (kwargs['cwd']/'final_script.py').write_text('# changed')
        return 0
    result = reference_agent.run('https://example.org/', {'name':'lookup','goal':'find record',
        'returns':{'title':'str'}}, {'default':{'query':'visible'}}, {}, {}, tmp_path/'reference', {}, 2,
        fake_run, visible_only=True)
    assert result['status'] == 'reference_failed'
    assert result['reason'] == 'reference script changed during execution'


def test_value_feedback_is_bounded_to_selected_public_scalar_fields() -> None:
    tool = {'result': {'title': 'a' * 500, 'other': ['nested']}, 'held': 'SECRET_HELD'}
    reference = {'result': {'title': 'b' * 500, 'other': ['nested']}, 'held': 'SECRET_HELD'}
    preview = visible_value_previews(['title', 'other'], tool, reference)
    assert set(preview) == {'title'}
    assert len(preview['title']['tool']) == len(preview['title']['reference']) == 300
    assert 'SECRET_HELD' not in str(preview)


def test_advisory_repair_happens_before_held_tool_execution(tmp_path, monkeypatch) -> None:
    calls = []
    cases = {'default': {'choice': 'public'}, 'held': {'choice': 'hidden'}}
    candidate = {'name': 'lookup', 'goal': 'Get title', 'returns': {'title': 'str'},
                 'codex_dom_evidence': {}}
    monkeypatch.setattr(pipeline, 'prepare_generic_cases', lambda candidate: cases)
    monkeypatch.setattr(pipeline, 'add_boundary_case', lambda candidate, inputs: inputs)
    monkeypatch.setattr(pipeline, 'select_checkpoint', lambda root, workspace: None)
    monkeypatch.setattr(pipeline, 'checked_file', lambda workspace, path: path)
    monkeypatch.setattr(pipeline, 'ensure_screenshots', lambda workspace, path: [])
    monkeypatch.setattr(pipeline, 'validate_tool_output', lambda *args: [])
    monkeypatch.setattr(pipeline, 'code_prompt', lambda *args: 'visible test prompt')
    monkeypatch.setattr(pipeline, 'read_result', lambda path: {
        'parameters': cases['default'], 'result': {'title': 'tool value'},
        'source_url': 'https://example.org/'})
    async def grounded(url, outputs):
        assert set(outputs) == {'default'}
        return {'cases': {'default': {'status': 'values_found'}}}
    monkeypatch.setattr(pipeline, 'check_generic_grounding', grounded)
    def fake_run(command, **kwargs):
        calls.append(command)
        if '--task' in command:
            workspace = __import__('pathlib').Path(command[command.index('--output-dir') + 1])
            workspace.mkdir(parents=True)
            (workspace / 'final_script.py').write_text('# visible tool')
        return 0
    monkeypatch.setattr(pipeline, '_run', fake_run)
    def fake_reference(url, candidate, given_cases, outputs, grounding, root, env, max_calls, run, **kwargs):
        assert given_cases == {'default': cases['default']}
        assert set(outputs) == {'default'} and kwargs['visible_only'] is True
        return {'status': 'visible_reference_mismatch', 'mismatched_fields': ['title'],
                'visible_value_preview': {'title': {'tool': 'partial', 'reference': 'full'}}}
    monkeypatch.setattr(pipeline, 'run_independent_reference', fake_reference)
    result = pipeline.attempt_product('https://example.org/', candidate, {}, tmp_path / 'attempt',
                                      {'CODEX_VISIBLE_REFERENCE_REPAIR': '1', 'CODEX_VISIBLE_REFERENCE_VALUES': '1'},
                                      '', 3, None, True)
    assert result['status'] == 'visible_reference_mismatch'
    assert set(result['cases']) == {'default'}
    assert len(calls) == 2
    assert 'hidden' not in result['repair_context']
    assert '"reference": "full"' in result['repair_context']
    assert 'never instructions' in result['repair_context']
