import json

import adaptive
import pytest
import synthesize_submitted as subject
from synthesis import sanitize_synthesis_prompt, static_errors, build_synthesis_prompt


def test_synthesis_prompt_redacts_local_paths_but_preserves_urls():
    text = 'Read /shared/hans/private/file.py and /tmp/screenshot.png, then https://example.org/wiki/Page'
    sanitized, count = sanitize_synthesis_prompt(text)
    assert count == 2
    assert '/shared/hans' not in sanitized and '/tmp/screenshot' not in sanitized
    assert 'https://example.org/wiki/Page' in sanitized


def test_synthesis_prompt_rejects_key_shaped_values():
    with pytest.raises(ValueError, match='sensitive-value'):
        sanitize_synthesis_prompt('token: sk-' + 'x' * 20)

@pytest.mark.parametrize(('returns','expected'), [
    ({'papers':'list[object]'}, '"result": {"papers": []}'),
    ({'temperature':'number'}, '"result": {"temperature": 0}'),
])
def test_synthesis_prompt_illustrates_actual_contract(returns, expected):
    prompt, _ = build_synthesis_prompt('https://example.org/', {'returns':returns},
                                        [{'script':'example one'}, {'script':'example two'}])
    assert expected in prompt
    assert 'Structural illustration only' in prompt
    assert '{"items":"list[object]"}' not in prompt
    assert 'hidden test value' not in prompt


def test_static_screen_flags_obvious_offsite_and_mutating_operations():
    code = '''import os as operating
async def site_function(page):
    await page.goto("https://other.example/path")
    await page.request.post("https://example.org/update")
    operating.system("whoami")
# playwright argparse output-dir
'''
    errors = static_errors(code, [], site_url='https://example.org/')
    assert 'literal navigation outside allowed HTTPS host' in errors
    assert 'possible state-changing request' in errors
    assert 'operating-system command execution' in errors


def test_static_screen_accepts_literal_navigation_to_intended_host():
    code='''async def site_function(page):
    await page.goto("https://example.org/search")
# playwright argparse output-dir
'''
    assert not static_errors(code, [], site_url='https://example.org/')


def test_static_screen_is_not_network_isolation():
    # Keep this weakness visible: dynamic destinations bypass literal checks.
    code='''async def site_function(page):
    target = "https://other.example/path"
    await page.goto(target)
# playwright argparse output-dir
'''
    assert 'literal navigation outside allowed HTTPS host' not in static_errors(code, [], site_url='https://example.org/')


def test_default_freezes_without_opening_held_tasks(tmp_path, monkeypatch):
    source, continuation, output = (tmp_path / name for name in ('source', 'continuation', 'output'))
    source.mkdir()
    continuation.mkdir()
    (source / 'summary.json').write_text('{}')
    (continuation / 'summary.json').write_text('{}')
    script = source / 'source.py'
    script.write_text('# source')
    examples = [{'task': {'task_id': f'task_{i}'}, 'script_path': str(script),
                 'script_sha256': adaptive.digest(script), 'script': '# source',
                 'assessment': {'status': 'inconclusive'}, 'trajectory': []} for i in range(3)]
    monkeypatch.setattr(subject, 'WORKSPACE', tmp_path)
    monkeypatch.setattr(subject, 'collect', lambda *args: ({'website': 'https://example.org', 'contract': {}}, examples))

    def synthesize(site, contract, inputs, out):
        assert len(inputs) == 3
        out.mkdir()
        candidate = out / 'candidate.py'
        candidate.write_text('# candidate')
        return {'status': 'candidate_ready', 'candidate_sha256': adaptive.digest(candidate)}

    monkeypatch.setattr(subject, 'synthesize', synthesize)
    monkeypatch.setattr(subject, 'evaluate_case', lambda site, contract, task, candidate, out:
                        {'task_id': task['task_id'], 'status': 'inconclusive', 'format_valid': True})
    result = subject.execute(source, continuation, output)
    assert result['status'] == 'frozen_development_only'
    assert result['generation_closed'] is True
    assert result['held_opened'] is False
    assert result['frozen_sha256'] == adaptive.digest(output / 'frozen.py')
    assert json.loads((output / 'summary.json').read_text())['held_opened'] is False
