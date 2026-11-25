"""Host-only session validation. Never include state contents in diagnostics."""
import json
from pathlib import Path
import stat
from urllib.parse import urlparse

SESSION_ROOT = Path('/shared/hans/.secrets/browser_sessions')


def session_file(directory, website, *, root=SESSION_ROOT):
    directory = Path(directory)
    parent = root.resolve(strict=True)
    if directory.is_symlink() or directory.resolve(strict=True).parent != parent:
        raise ValueError('session must be a direct private browser-session directory')
    if directory.stat().st_mode & 0o077:
        raise ValueError('session directory permissions are too broad')
    metadata = directory/'metadata.json'
    path = directory/'storage_state.json'
    if metadata.is_symlink() or path.is_symlink():
        raise ValueError('session symlinks are not allowed')
    info = json.loads(metadata.read_text())
    expected, actual = urlparse(website), urlparse(info['website'])
    if (expected.scheme, expected.hostname, expected.port) != (actual.scheme, actual.hostname, actual.port):
        raise ValueError('session website does not match selected website')
    if not info.get('login_cookie_received'):
        raise ValueError('session has no confirmed login marker')
    details = path.stat()
    if not stat.S_ISREG(details.st_mode) or details.st_mode & 0o077 or details.st_size > 2_000_000:
        raise ValueError('session file type, size or permissions invalid')
    data = json.loads(path.read_text())
    if not isinstance(data.get('cookies'), list) or not isinstance(data.get('origins'), list):
        raise ValueError('invalid browser storage state')
    return path.resolve(strict=True)
