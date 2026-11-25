"""Conservative, site-neutral outage signal from repeated agent command logs.

This detects infrastructure evidence, not task correctness. A match can be
transient or caused by the execution environment; stop rather than guess.
"""
from pathlib import Path
from urllib.parse import urlparse

ERROR_MARKERS = ('err_connection_refused','ns_error_connection_refused',
                 'err_name_not_resolved','err_network_changed',
                 'network is unreachable','temporary failure in name resolution')


def repeated_access_errors(workspace, url, minimum=3):
    root=Path(workspace).resolve()
    host=urlparse(url).hostname
    if not host:
        return {'detected':False,'distinct_failed_steps':0}
    logs=root/'logs'
    count=0
    for path in logs.glob('step_*.log') if logs.is_dir() else ():
        try:
            if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root) or path.stat().st_size>1_000_000:
                continue
            text=path.read_text(encoding='utf-8',errors='replace').casefold()
        except OSError:
            continue
        if host.casefold() in text and any(marker in text for marker in ERROR_MARKERS):
            count+=1
    return {'detected':count>=minimum,'distinct_failed_steps':count}
