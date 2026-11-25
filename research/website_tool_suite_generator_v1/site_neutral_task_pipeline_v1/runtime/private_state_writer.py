"""Container-only private bootstrap; consumes stdin, emits no session contents."""
import json
import os
from pathlib import Path
import sys

os.umask(0o077)
data=sys.stdin.buffer.read(2_000_001)
if len(data)>2_000_000:
    raise RuntimeError('private browser state exceeds bound')
try:
    value=json.loads(data)
    if not isinstance(value.get('cookies'),list) or not isinstance(value.get('origins'),list):
        raise ValueError()
except Exception:
    raise RuntimeError('invalid private browser state') from None
root=Path('/tmp/ww_private_session')
root.mkdir(mode=0o700,exist_ok=True)
(root/'state.json').write_bytes(data)
(root/'state.json').chmod(0o600)
