import json
import pytest
import prepare_recovery as recovery


def setup_source(tmp_path, monkeypatch):
    monkeypatch.setattr(recovery, 'WORKSPACE', tmp_path)
    source = tmp_path / 'source'
    batch = source / 'development_rounds' / 'round_02'
    batch.mkdir(parents=True)
    (source / 'summary.json').write_text(json.dumps({'held_opened': False, 'status': 'running', 'website': 'https://example.org/'}))
    (batch / 'summary.json').write_text(json.dumps({'tasks': [{'task_id': 'task_1'}, {'task_id': 'task_2'}],
        'attempts': [{'task_id': 'task_1', 'trajectory_status': 'Submitted', 'process_exit': 0}]}))
    return source, batch


def test_inventory_does_not_promote_or_open_held(tmp_path, monkeypatch):
    source, batch = setup_source(tmp_path, monkeypatch)
    script = batch / 'sample.py'
    script.write_text('print(1)')
    monkeypatch.setattr(recovery, 'select_development_candidate',
                        lambda root, task, record: (script, 'submitted') if task['task_id'] == 'task_1' else (None, 'no_candidate'))
    result = recovery.inventory(source, 2)
    assert result['status'] == 'inventory_only_not_assessed'
    assert result['held_out_opened_by_inventory'] is False
    assert result['model_calls'] == 0
    assert result['candidates'][0]['assessment'] == 'not_run'
    assert len(result['candidates'][0]['script_sha256']) == 64
    assert result['candidates'][1]['candidate_origin'] == 'no_candidate'


def test_inventory_refuses_unknown_holdout_state(tmp_path, monkeypatch):
    source, _ = setup_source(tmp_path, monkeypatch)
    (source / 'summary.json').write_text(json.dumps({'held_opened': True}))
    with pytest.raises(ValueError, match='held'):
        recovery.inventory(source, 2)


def test_inventory_rejects_duplicate_task_ids(tmp_path, monkeypatch):
    source, batch = setup_source(tmp_path, monkeypatch)
    (batch / 'summary.json').write_text(json.dumps({'tasks': [{'task_id': 'same'}, {'task_id': 'same'}], 'attempts': []}))
    with pytest.raises(ValueError, match='duplicate'):
        recovery.inventory(source, 2)
