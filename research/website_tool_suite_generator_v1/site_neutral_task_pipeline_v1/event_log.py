"""Host-only, append-only timeline shared by concurrent pipeline stages.

Only controlled metadata belongs here. Requests, model text and page evidence
live in their own artifacts; cookies, tokens and environment dumps never belong
in this index. A missing completion event means interrupted/unknown, not success.
"""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import threading
import time
from uuid import uuid4


def emit(path, stage, event, **metadata):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {'event_id': uuid4().hex, 'utc': datetime.now(timezone.utc).isoformat(),
           'monotonic_ns': time.monotonic_ns(), 'pid': os.getpid(),
           'thread': threading.current_thread().name, 'stage': stage, 'event': event,
           **metadata}
    line = json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n'
    # flock also serializes other host processes, not just threads in this run.
    with path.open('a', encoding='utf-8') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(line)
        stream.flush()
        os.fsync(stream.fileno())
        fcntl.flock(stream, fcntl.LOCK_UN)
    return row['event_id']


def run_timeline(output):
    """Find an existing pipeline root without creating one in a worker workspace."""
    output = Path(output)
    for parent in (output, *output.parents):
        if (parent/'summary.json').is_file() and (parent/'.config').is_dir():
            return parent/'events.jsonl'
    return output/'events.jsonl'
