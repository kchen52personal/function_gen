"""Normalize two common screenshot layouts without changing generated code."""
from pathlib import Path
import shutil

from codex_artifact_guard import checked_file


def ensure_screenshots(workspace: Path, output: Path) -> list[str]:
    errors = []
    try:
        root = workspace.resolve(strict=True)
        out = output.resolve(strict=True)
        if root not in out.parents:
            raise ValueError('output directory escapes workspace')
        directory = out / 'screenshots'
        directory.mkdir(exist_ok=True)
        if root not in directory.resolve(strict=True).parents:
            raise ValueError('screenshot directory escapes workspace')
    except (OSError, ValueError) as error:
        return [f'invalid screenshot directory: {error}']
    for name in ('before.png', 'after.png'):
        target = directory / name
        try:
            if target.exists() or target.is_symlink():
                if not checked_file(root, target).stat().st_size:
                    raise ValueError('empty screenshot')
                continue
            alternative = checked_file(root, out / name)
            if not alternative.stat().st_size:
                raise ValueError('empty screenshot')
            shutil.copyfile(alternative, target)
            checked_file(root, target)
        except (OSError, ValueError):
            errors.append(f'missing or invalid screenshot: {name}')
    return errors
