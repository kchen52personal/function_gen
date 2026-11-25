"""Keep untrusted generated artifacts inside their own run workspace."""
from pathlib import Path


def checked_file(workspace: Path, path: Path) -> Path:
    root = workspace.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if root not in resolved.parents:
        raise ValueError('generated artifact escapes its workspace')
    if not resolved.is_file():
        raise ValueError('generated artifact is not a regular file')
    if resolved.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('generated artifact exceeds the 16 MiB size limit')
    return resolved
