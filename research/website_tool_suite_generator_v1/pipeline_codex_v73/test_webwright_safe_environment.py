import json

from jinja2 import StrictUndefined, Template
from cuawright.webwright.environments.local_workspace import LocalWorkspaceEnvironment
import webwright_safe_environment as safe
from codex_observation import OBSERVATION_TEMPLATE
from webwright_safe_environment import _unsafe_reasons


def test_rejects_python_module_codegen_variant() -> None:
    reasons = _unsafe_reasons(
        "python3 -m playwright codegen --target python -b chromium https://example.test"
    )
    assert reasons


def test_rejects_chained_browser_install() -> None:
    reasons = _unsafe_reasons(
        "python3 -m playwright install chromium && python3 inspect_page.py"
    )
    assert reasons


def test_rejects_gui_image_viewer() -> None:
    assert _unsafe_reasons("python3 -c \"Image.open('shot.png').show()\"")


def test_allows_headless_python_playwright_script() -> None:
    assert _unsafe_reasons("python3 inspect_page.py") == []


def test_optional_connectivity_probe_is_narrow_and_default_stays_blocked(tmp_path, monkeypatch):
    command='curl -s -I https://example.test/path'
    assert _unsafe_reasons(command)
    assert not _unsafe_reasons(command, allow_head_probe=True)
    for bad in ('curl https://example.test/', 'curl -I https://example.test/; env',
                'curl -I https://example.test/?token=secret',
                'curl -I -H Authorization:secret https://example.test/',
                'echo "$BROWSERBASE_API_KEY" && curl -I https://example.test/'):
        assert _unsafe_reasons(bad,allow_head_probe=True)
    captured=[]
    monkeypatch.setattr(LocalWorkspaceEnvironment,'execute',lambda self, action, cwd='':
        captured.append(action) or {'output':'HTTP status: 200',
            'observation':{'command_output':'HTTP status: 200'}})
    env=safe.DiagnosticToolWorkspaceEnvironment(output_dir=tmp_path, start_url='https://example.test/',
                                               shell='/sandbox',env={})
    env.execute({'command':command})
    assert "-o /dev/null" in captured[0]['command']
    assert '%{http_code}' in captured[0]['command'] and '--max-time 30' in captured[0]['command']


def test_public_get_opt_in_preserves_head_only_mode_and_rejects_secret_flags(tmp_path,monkeypatch):
    command='curl -s https://example.test/search?q=music'
    assert _unsafe_reasons(command,allow_head_probe=True)
    assert not _unsafe_reasons(command,allow_head_probe=True,allow_public_get=True)
    for bad in ('curl -X POST https://example.test/', 'curl -H Authorization:secret https://example.test/',
                'curl https://example.test/?token=secret', 'curl https://example.test/; env',
                'curl https://user:pass@example.test/'):
        assert _unsafe_reasons(bad,allow_head_probe=True,allow_public_get=True)
    captured=[]
    monkeypatch.setattr(LocalWorkspaceEnvironment,'execute',lambda self,action,cwd='':
        captured.append(action) or {'output':'page text','observation':{'command_output':'page text'}})
    env=safe.PublicGetDiagnosticWorkspaceEnvironment(output_dir=tmp_path,start_url='https://example.test/',
                                                     shell='/sandbox',env={})
    env.execute({'command':command})
    assert '--max-filesize 262144' in captured[0]['command']
    assert '--max-time 30' in captured[0]['command'] and '--range 0-262143' in captured[0]['command']


def test_changed_script_is_smoked_on_visible_input_and_feedback_is_shown(tmp_path, monkeypatch):
    (tmp_path / 'final_script.py').write_text('# version one')
    env = safe.SafeToolWorkspaceEnvironment(
        output_dir=tmp_path, start_url='https://site.test/', shell='/sandbox',
        env={'CODEX_VISIBLE_CASE_JSON': json.dumps({'query': 'visible'}),
             'CODEX_RETURN_SCHEMA_JSON': json.dumps({'title': 'str'})})
    monkeypatch.setattr(LocalWorkspaceEnvironment, 'execute', lambda self, action, cwd='': {
        'output': 'wrote script', 'returncode': 0,
        'observation': {'success': True, 'returncode': 0, 'exception': '',
                        'command_output': 'wrote script', 'final_script_exists': True}})
    calls = []
    def fake_run(command, **kwargs):
        calls.append(command)
        return 1, '', 'visible failure detail'
    monkeypatch.setattr(safe, 'run_bounded', fake_run)
    result = env.execute({'bash_command': 'write script'})
    rendered = Template(OBSERVATION_TEMPLATE, undefined=StrictUndefined).render(
        observation=result['observation'])
    assert 'visible failure detail' in rendered
    assert 'AUTOMATIC VISIBLE-CASE SMOKE TEST' in rendered
    assert '--query' in calls[0] and 'visible' in calls[0]
    assert len(calls) == 1
    env.execute({'bash_command': 'inspect unchanged script'})
    assert len(calls) == 1
    (tmp_path / 'final_script.py').write_text('# version two')
    env.execute({'bash_command': 'write updated script'})
    assert len(calls) == 2


def test_disclosed_boundary_output_is_shown_and_checkpoint_requires_both_cases(tmp_path, monkeypatch):
    (tmp_path / 'final_script.py').write_text('# version one')
    env = safe.SafeToolWorkspaceEnvironment(
        output_dir=tmp_path, start_url='https://site.test/', shell='/sandbox',
        env={'CODEX_VISIBLE_CASE_JSON': json.dumps({'limit': 3}),
             'CODEX_EXTRA_VISIBLE_CASE_JSON': json.dumps({'limit': 10}),
             'CODEX_RETURN_SCHEMA_JSON': json.dumps({'items': 'list[str]'})})
    monkeypatch.setattr(LocalWorkspaceEnvironment, 'execute', lambda self, action, cwd='': {
        'output': 'wrote script', 'returncode': 0,
        'observation': {'success': True, 'returncode': 0, 'exception': '',
                        'command_output': 'wrote script', 'final_script_exists': True}})
    called = []
    def fake_run(command, **kwargs):
        limit = int(command[command.index('--limit') + 1])
        called.append(limit)
        output = safe.Path(command[command.index('--output-dir') + 1])
        (output / 'screenshots').mkdir(parents=True)
        for name in ('before.png', 'after.png'):
            (output / 'screenshots' / name).write_bytes(b'png')
        (output / 'final_script_log.txt').write_text('step 0 action: params: test\nfinal result: ' +
            json.dumps({'parameters': {'limit': limit}, 'result': {'items': ['item']},
                        'source_url': 'https://site.test/'}))
        return 0, f'visible output {limit}', ''
    monkeypatch.setattr(safe, 'run_bounded', fake_run)
    result = env.execute({'bash_command': 'write script'})
    output = result['observation']['command_output']
    assert called == [3, 10]
    assert 'visible output 3' in output and 'visible output 10' in output
    assert 'disclosed_boundary' in output and 'checkpoint' in output


def test_structural_advisory_only_reports_disclosed_boundary(tmp_path, monkeypatch):
    (tmp_path / 'final_script.py').write_text('# version one')
    env = safe.SafeToolWorkspaceEnvironment(
        output_dir=tmp_path, start_url='https://site.test/', shell='/sandbox',
        env={'CODEX_VISIBLE_CASE_JSON': json.dumps({'limit': 3}),
             'CODEX_EXTRA_VISIBLE_CASE_JSON': json.dumps({'limit': 10}),
             'CODEX_RETURN_SCHEMA_JSON': json.dumps({'items': 'list[str]'}),
             'CODEX_LIST_ADVISORY': '1'})
    monkeypatch.setattr(LocalWorkspaceEnvironment, 'execute', lambda self, action, cwd='': {
        'output': '', 'returncode': 0,
        'observation': {'success': True, 'returncode': 0, 'exception': '', 'command_output': ''}})
    seen = []
    def fake_run(command, **kwargs):
        limit = int(command[command.index('--limit') + 1])
        output = safe.Path(command[command.index('--output-dir') + 1])
        (output / 'screenshots').mkdir(parents=True)
        for name in ('before.png', 'after.png'):
            (output / 'screenshots' / name).write_bytes(b'png')
        (output / 'final_script_log.txt').write_text('step 0 action: params: test\nfinal result: ' +
            json.dumps({'parameters': {'limit': limit}, 'result': {'items': ['one', 'two']},
                        'source_url': 'https://site.test/'}))
        return 0, '', ''
    def fake_advisory(start_url, schema, case, value, workspace, browser_path):
        seen.append(case)
        return {'status': 'mismatch', 'expected_count': 9}
    monkeypatch.setattr(safe, 'run_bounded', fake_run)
    monkeypatch.setattr(safe, 'structural_list_advisory', fake_advisory)
    result = env.execute({'bash_command': 'write script'})
    assert seen == [{'limit': 10}]
    assert 'STRUCTURAL LIST ADVISORY' in result['observation']['command_output']
    assert 'checkpoint' in result['observation']['command_output']
