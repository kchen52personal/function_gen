import json

import pytest

from codex_checkpoint import save_checkpoint, select_checkpoint


def test_visible_checkpoint_preserves_passing_script_after_bad_overwrite(tmp_path):
    workspace = tmp_path / 'agent'
    workspace.mkdir()
    script = workspace / 'final_script.py'
    script.write_text('print("passed")\n')
    digest = save_checkpoint(workspace, script)
    script.write_text('this is now broken')
    selected = select_checkpoint(tmp_path, workspace)
    assert selected.read_text() == 'print("passed")\n'
    assert script.read_text() == 'this is now broken'
    assert selected != script
    (tmp_path / 'codex_checkpoints' / f'{digest}.py').write_text('tampered')
    with pytest.raises(ValueError, match='hash mismatch'):
        select_checkpoint(tmp_path, workspace)


def test_checkpoint_fails_closed_on_invalid_metadata(tmp_path):
    workspace = tmp_path / 'agent'
    workspace.mkdir()
    store = tmp_path / 'codex_checkpoints'
    store.mkdir()
    (store / 'latest.json').write_text(json.dumps({'sha256': '../outside'}))
    with pytest.raises(ValueError, match='invalid'):
        select_checkpoint(tmp_path, workspace)
