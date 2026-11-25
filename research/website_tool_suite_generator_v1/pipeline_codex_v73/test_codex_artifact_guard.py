from pathlib import Path

import pytest

from codex_artifact_guard import checked_file
from webwright_safe_environment import SafeToolWorkspaceEnvironment


def test_generated_artifacts_cannot_resolve_outside_workspace(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    safe = workspace / 'result.txt'
    safe.write_text('safe')
    assert checked_file(workspace, safe) == safe
    outside = tmp_path / 'private.txt'
    outside.write_text('private test data')
    (workspace / 'link.txt').symlink_to(outside)
    with pytest.raises(ValueError, match='escapes'):
        checked_file(workspace, workspace / 'link.txt')
    (workspace / 'final_script.py').symlink_to(outside)
    env = SafeToolWorkspaceEnvironment(output_dir=workspace)
    assert env._final_script_path() == workspace / '.codex_rejected_script'


def test_oversized_generated_artifact_is_rejected(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    artifact = workspace / 'oversized.log'
    with artifact.open('wb') as file:
        file.truncate(16 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match='size limit'):
        checked_file(workspace, artifact)
