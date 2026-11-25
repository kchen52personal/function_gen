from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
import resume_interrupted as resume


def fixture_source(tmp_path, monkeypatch):
    monkeypatch.setattr(resume, 'WORKSPACE', tmp_path)
    monkeypatch.setattr(resume, 'PROJECT', tmp_path)
    source = tmp_path/'source'
    batch = source/'development_rounds/round_02'
    replacement = source/'replacements/round_02'
    held = source/'tasks/held_out'
    for p in (batch, replacement, held): p.mkdir(parents=True)
    tasks = [{'task_id': f'task_{n}', 'parameters': {'query': f'query {n}'}, 'intent': f'Find {n}'} for n in (1, 2)]
    (replacement/'tasks.json').write_text(json.dumps({'tasks': tasks}))
    (batch/'summary.json').write_text(json.dumps({'tasks': tasks, 'attempts': []}))
    (held/'tasks.json').write_text(json.dumps({'tasks': [{'task_id': f'held_{n}', 'parameters': {'query': f'held {n}'}} for n in range(4)]}))
    state = {'held_opened': False, 'checkpoints': [], 'development_attempts': [],
             'website': 'https://example.org/', 'idea': 'public search',
             'contract': {'returns': {'items': 'list[object]'}},
             'started_at': datetime.now(timezone.utc).isoformat(), 'limits': {'overall_seconds': 3600},
             'split_hashes': {'held_out': resume.digest(held/'tasks.json')}}
    (source/'summary.json').write_text(json.dumps(state))
    candidates = []
    for task in tasks:
        script = batch/task['task_id']/'qwen3.5-plus/agent/final_script.py'
        script.parent.mkdir(parents=True)
        script.write_text('print("candidate")')
        candidates.append({'task_id': task['task_id'], 'candidate_origin': 'submitted', 'assessment': 'not_run',
                           'script_path': str(script), 'script_sha256': resume.digest(script)})
    manifest = {'status': 'inventory_only_not_assessed', 'held_out_opened_by_inventory': False,
                'source_run': str(source), 'round': 2, 'source_summary_sha256': resume.digest(source/'summary.json'),
                'batch_summary_sha256': resume.digest(batch/'summary.json'), 'candidates': candidates}
    manifest_path = tmp_path/'inventory.json'
    manifest_path.write_text(json.dumps(manifest))
    return source, held/'tasks.json', manifest_path, candidates


def test_resume_freezes_before_opening_held(tmp_path, monkeypatch):
    source, held_path, manifest, candidates = fixture_source(tmp_path, monkeypatch)
    out = tmp_path/'continuation'
    calls = []
    original_digest = resume.digest
    def guarded_digest(path):
        if Path(path) == held_path:
            state = json.loads((out/'summary.json').read_text())
            assert state['generation_closed'] is True
            assert state['checkpoints'][0]['status'] == 'frozen_before_any_held'
        return original_digest(path)
    def assess(url, contract, task, path, output, **kwargs):
        assert kwargs['reference_model'] == 'gpt-4.1'
        calls.append(task['task_id'])
        return {'task_id': task['task_id'], 'status': 'model_agreement'}
    def synthesize(url, contract, examples, output):
        assert len(examples) == 2
        output.mkdir(parents=True)
        candidate = output/'candidate.py'
        candidate.write_text('print("frozen")')
        return {'status': 'candidate_ready', 'candidate_sha256': original_digest(candidate)}
    monkeypatch.setattr(resume, 'digest', guarded_digest)
    monkeypatch.setattr(resume, 'evaluate_case', assess)
    monkeypatch.setattr(resume, 'synthesize', synthesize)
    result = resume.execute(manifest, out, reference_model='gpt-4.1')
    assert result['status'] == 'complete_exploratory_checkpoint_comparison'
    assert result['held_opened'] is True
    assert result['checkpoints'][0]['held_model_agreements'] == 4
    assert calls[:4] == ['task_1', 'task_2', 'task_1', 'task_2']
    assert calls[4:] == [f'held_{n}' for n in range(4)]
    assert (source/'summary.json').exists()


def test_resume_fails_closed_on_changed_candidate(tmp_path, monkeypatch):
    source, _, manifest, candidates = fixture_source(tmp_path, monkeypatch)
    Path(candidates[0]['script_path']).write_text('changed')
    with pytest.raises(ValueError, match='hash changed'):
        resume.load_recovery(manifest)


def test_resume_fails_closed_if_source_summary_changes(tmp_path, monkeypatch):
    source, _, manifest, candidates = fixture_source(tmp_path, monkeypatch)
    (source/'summary.json').write_text('{}')
    with pytest.raises(ValueError, match='summary changed'):
        resume.load_recovery(manifest)


def test_resume_refuses_already_opened_held_set(tmp_path, monkeypatch):
    source, _, manifest, candidates = fixture_source(tmp_path, monkeypatch)
    state = json.loads((source/'summary.json').read_text())
    state['held_opened'] = True
    (source/'summary.json').write_text(json.dumps(state))
    data = json.loads(manifest.read_text())
    data['source_summary_sha256'] = resume.digest(source/'summary.json')
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='opened held'):
        resume.load_recovery(manifest)


def test_retry_reuses_prior_assessments_and_creates_development_parent(tmp_path, monkeypatch):
    source, held_path, manifest_path, candidates = fixture_source(tmp_path, monkeypatch)
    batch_path = source/'development_rounds/round_02/summary.json'
    task_path = source/'replacements/round_02/tasks.json'
    extra = [{'task_id': f'task_{n}', 'parameters': {'query': f'query {n}'}, 'intent': f'Find {n}'} for n in (3, 4)]
    for path in (batch_path, task_path):
        value = json.loads(path.read_text())
        value['tasks'].extend(extra)
        path.write_text(json.dumps(value))
    manifest = json.loads(manifest_path.read_text())
    manifest['batch_summary_sha256'] = resume.digest(batch_path)
    manifest['candidates'].extend([{'task_id': x['task_id'], 'candidate_origin': 'no_candidate',
                                    'assessment': 'not_run'} for x in extra])
    manifest_path.write_text(json.dumps(manifest))
    prior = tmp_path/'old_continuation/summary.json'
    prior.parent.mkdir()
    assessments = [{**candidate, 'status': 'inconclusive', 'reason': 'reference unavailable'} for candidate in candidates]
    assessments.extend([{'task_id': x['task_id'], 'status': 'not_assessable', 'error_type': 'FileNotFoundError'} for x in extra])
    prior.write_text(json.dumps({'status': 'stopped_no_frozen_checkpoint', 'held_opened': False, 'checkpoints': [],
        'machine_agreed_development_count': 0, 'source_run': str(source), 'recovery_manifest': str(manifest_path),
        'recovery_manifest_sha256': resume.digest(manifest_path), 'reference_model': 'gpt-4.1', 'assessments': assessments}))
    calls = []
    def fake_run(url, contract, tasks, output, **kwargs):
        assert output.parent.is_dir()
        calls.append(tasks[0]['task_id'])
        output.mkdir()
        (output/'summary.json').write_text(json.dumps({'attempts': []}))
    monkeypatch.setattr(resume.development, 'run', fake_run)
    monkeypatch.setattr(resume.adaptive, 'select_development_candidate', lambda *args: (None, 'no_candidate'))
    def forbidden_assessment(*args, **kwargs):
        raise AssertionError('completed prior assessments must not be repeated')
    monkeypatch.setattr(resume, 'evaluate_case', forbidden_assessment)
    result = resume.execute(manifest_path, tmp_path/'new_continuation', reference_model='gpt-4.1', prior_continuation=prior)
    assert calls == ['task_3', 'task_4']
    assert result['held_opened'] is False
    assert len(result['assessments']) == 4
    assert all(x.get('carried_from_summary') == str(prior) for x in result['assessments'][:2])
    assert len(result['prior_startup_errors']) == 2
