import json
import threading

import pytest

import run_verified_site as pipeline


def test_failed_checkpoint_replace_keeps_previous_data(tmp_path, monkeypatch) -> None:
    path = tmp_path / 'summary.json'
    pipeline.write(path, {'version': 'old'})
    def fail_replace(source, target):
        raise OSError('simulated interrupted replace')
    monkeypatch.setattr(pipeline.os, 'replace', fail_replace)
    with pytest.raises(OSError, match='simulated'):
        pipeline.write(path, {'version': 'new'})
    assert json.loads(path.read_text()) == {'version': 'old'}
    assert list(tmp_path.glob('.summary.json.*.tmp'))


def test_readers_see_complete_checkpoints_during_updates(tmp_path) -> None:
    path = tmp_path / 'summary.json'
    pipeline.write(path, {'version': 0, 'data': 'x' * 10000})
    errors = []
    done = threading.Event()
    def reader():
        while not done.is_set():
            try:
                value = json.loads(path.read_text())
                if value['data'] != 'x' * 10000:
                    errors.append('wrong data')
            except (OSError, ValueError, KeyError) as error:
                errors.append(str(error))
    thread = threading.Thread(target=reader)
    thread.start()
    try:
        for version in range(1, 30):
            pipeline.write(path, {'version': version, 'data': 'x' * 10000})
    finally:
        done.set()
        thread.join()
    assert not errors
    assert json.loads(path.read_text())['version'] == 29
