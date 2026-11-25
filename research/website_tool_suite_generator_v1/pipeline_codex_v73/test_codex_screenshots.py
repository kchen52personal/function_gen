from codex_screenshots import ensure_screenshots


def test_normalizes_root_screenshots_and_rejects_missing_or_external(tmp_path):
    workspace = tmp_path / 'agent'
    output = workspace / 'run'
    output.mkdir(parents=True)
    (output / 'before.png').write_bytes(b'before')
    (output / 'after.png').write_bytes(b'after')
    assert ensure_screenshots(workspace, output) == []
    assert (output / 'screenshots' / 'before.png').read_bytes() == b'before'
    assert (output / 'screenshots' / 'after.png').read_bytes() == b'after'
    other = workspace / 'other'
    other.mkdir()
    assert len(ensure_screenshots(workspace, other)) == 2
    outside = tmp_path / 'outside'
    outside.mkdir()
    assert ensure_screenshots(workspace, outside)
