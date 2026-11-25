"""Best-effort process identity for diagnosing stale experiment summaries."""
import os
from pathlib import Path
import socket


def proc_start_ticks(pid):
    text=(Path('/proc')/str(pid)/'stat').read_text()
    # comm is parenthesized and may contain spaces or right parentheses.
    suffix=text.rsplit(')',1)[1].split()
    return int(suffix[19])  # /proc/<pid>/stat field 22 (starttime)


def current_identity():
    pid=os.getpid()
    try:
        start=proc_start_ticks(pid)
    except (OSError,ValueError,IndexError):
        start=None
    return {'pid':pid,'hostname':socket.gethostname(),'linux_start_ticks':start}


def inspect_identity(identity):
    if not isinstance(identity,dict):
        return {'status':'unknown','reason':'run predates process identity tracking'}
    if identity.get('hostname')!=socket.gethostname():
        return {'status':'unknown','reason':'recorded process is on a different host or hostname namespace'}
    pid, expected=identity.get('pid'),identity.get('linux_start_ticks')
    if type(pid) is not int or type(expected) is not int:
        return {'status':'unknown','reason':'incomplete recorded process identity'}
    try:
        actual=proc_start_ticks(pid)
    except FileNotFoundError:
        return {'status':'not_visible','reason':'process is not visible; it may have exited or be in a different PID namespace'}
    except (OSError,ValueError,IndexError):
        return {'status':'unknown','reason':'cannot inspect the recorded process'}
    if actual!=expected:
        return {'status':'identity_mismatch','reason':'PID exists but has a different start time'}
    return {'status':'visible','reason':'PID and start time match; this does not guarantee progress'}
