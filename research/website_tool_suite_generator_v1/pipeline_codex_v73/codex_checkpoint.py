"""Keep the most recent visible-smoke-passing generated script, unedited."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

from codex_artifact_guard import checked_file


def save_checkpoint(workspace: Path, script: Path) -> str:
    data = checked_file(workspace, script).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    store = workspace.parent / 'codex_checkpoints'
    store.mkdir(parents=True, exist_ok=True)
    (store / f'{digest}.py').write_bytes(data)
    (store / 'latest.json').write_text(json.dumps({'sha256': digest}) + '\n', encoding='utf-8')
    return digest


def select_checkpoint(root: Path, workspace: Path) -> Path | None:
    metadata = root / 'codex_checkpoints' / 'latest.json'
    if not metadata.exists():
        return None
    record = json.loads(checked_file(root, metadata).read_text(encoding='utf-8'))
    digest = record.get('sha256')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('invalid visible-smoke checkpoint digest')
    source = checked_file(root, root / 'codex_checkpoints' / f'{digest}.py')
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('visible-smoke checkpoint hash mismatch')
    selected_dir = workspace / f'codex_selected_{uuid4().hex}'
    selected_dir.mkdir(exist_ok=False)
    script = selected_dir / 'final_script.py'
    script.write_bytes(data)
    return script
