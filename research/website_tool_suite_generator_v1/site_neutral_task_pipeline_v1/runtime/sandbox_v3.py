#!/shared/miniconda3/bin/python
"""Confine generated code; allow WebWright's read-only package and /dev/null."""
from __future__ import annotations
import ctypes
import os
from pathlib import Path
import sys

LIBC = ctypes.CDLL(None, use_errno=True)
CREATE_RULESET, ADD_RULE, RESTRICT_SELF, PR_SET_NO_NEW_PRIVS = 444, 445, 446, 38
EXECUTE, WRITE_FILE, READ_FILE, READ_DIR = 1, 2, 4, 8
ALL = EXECUTE | WRITE_FILE | READ_FILE | READ_DIR | 16 | 32 | sum(1 << i for i in range(6, 13))
READ = EXECUTE | READ_FILE | READ_DIR
PROJECT = Path('/shared/hans/research/website_tool_suite_generator_v1')


class Ruleset(ctypes.Structure):
    _fields_ = [('handled_access_fs', ctypes.c_uint64)]


class PathBeneath(ctypes.Structure):
    _fields_ = [('allowed_access', ctypes.c_uint64), ('parent_fd', ctypes.c_int32)]


def check(value: int, operation: str) -> int:
    if value < 0:
        err = ctypes.get_errno()
        raise OSError(err, f'{operation}: {os.strerror(err)}')
    return value


def confine(workspace: Path) -> None:
    workspace = workspace.resolve(strict=True)
    if PROJECT not in workspace.parents:
        raise ValueError('sandbox workspace must be inside the website-tool project')
    fd = check(LIBC.syscall(CREATE_RULESET, ctypes.byref(Ruleset(ALL)), ctypes.sizeof(Ruleset), 0), 'create ruleset')
    try:
        rules = [(workspace, ALL), (Path('/dev/null'), READ_FILE | WRITE_FILE),
                 (PROJECT / '.playwright-browsers', READ), (PROJECT / 'vendor/Webwright/src', READ)]
        rules += [(Path(p), READ) for p in ('/usr', '/bin', '/lib', '/lib64', '/etc', '/dev',
                                              '/proc', '/sys', '/run/systemd/resolve', '/shared/miniconda3')]
        for path, access in rules:
            if not path.exists():
                continue
            parent = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                check(LIBC.syscall(ADD_RULE, fd, 1, ctypes.byref(PathBeneath(access, parent)), 0), 'add rule')
            finally:
                os.close(parent)
        check(LIBC.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0), 'no new privileges')
        check(LIBC.syscall(RESTRICT_SELF, fd, 0), 'restrict self')
    finally:
        os.close(fd)


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] != '--inside-namespace':
        os.execve('/usr/bin/unshare', ['/usr/bin/unshare', '--user', '--map-root-user', '--mount', '--pid',
                  '--fork', '--mount-proc', sys.executable, __file__, '--inside-namespace', *sys.argv[1:]],
                  {'WORKSPACE_DIR': os.environ['WORKSPACE_DIR']})
    workspace = Path(os.environ['WORKSPACE_DIR']).resolve(strict=True)
    command = sys.argv[2:]
    if not command:
        raise ValueError('missing command')
    for name in ('.tmp', '.cache', '.config'):
        (workspace / name).mkdir(exist_ok=True)
    confine(workspace)
    env = {'PATH': '/shared/miniconda3/bin:/usr/bin:/bin', 'WORKSPACE_DIR': str(workspace),
           'TMPDIR': str(workspace / '.tmp'), 'XDG_CACHE_HOME': str(workspace / '.cache'),
           'XDG_CONFIG_HOME': str(workspace / '.config'),
           'PLAYWRIGHT_BROWSERS_PATH': str(PROJECT / '.playwright-browsers'),
           'PYTHONPATH': f'{workspace}:{PROJECT / "vendor/Webwright/src"}',
           'PYTHONDONTWRITEBYTECODE': '1', 'LANG': 'C.UTF-8'}
    if command[0] == '-c':
        command = ['/bin/bash', *command]
    os.execvpe(command[0], command, env)


if __name__ == '__main__':
    main()
