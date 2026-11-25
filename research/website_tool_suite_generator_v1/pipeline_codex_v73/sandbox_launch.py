#!/shared/miniconda3/bin/python
"""Execute generated browser code with Landlock filesystem confinement.

The model backend stays outside this process. Generated commands can access
their run directory and read the Python/browser runtime, but not project data
or API keys. Network access remains available for the browser.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys

LIBC = ctypes.CDLL(None, use_errno=True)
CREATE_RULESET, ADD_RULE, RESTRICT_SELF = 444, 445, 446
PR_SET_NO_NEW_PRIVS = 38
EXECUTE, WRITE_FILE, READ_FILE, READ_DIR = 1, 2, 4, 8
REMOVE_DIR, REMOVE_FILE = 16, 32
MAKE_RIGHTS = sum(1 << i for i in range(6, 13))
ALL = EXECUTE | WRITE_FILE | READ_FILE | READ_DIR | REMOVE_DIR | REMOVE_FILE | MAKE_RIGHTS
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
        raise ValueError('sandbox workspace must be within the website-tool project')
    ruleset = Ruleset(ALL)
    fd = check(LIBC.syscall(CREATE_RULESET, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0),
               'landlock_create_ruleset')
    try:
        for path, access in [(workspace, ALL),
            *[(Path(p), READ) for p in ('/usr', '/bin', '/lib', '/lib64', '/etc',
                                        '/dev', '/proc', '/sys', '/run/systemd/resolve',
                                        '/shared/miniconda3')],
            (PROJECT / '.playwright-browsers', READ)]:
            if not path.exists():
                continue
            parent = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = PathBeneath(access, parent)
                check(LIBC.syscall(ADD_RULE, fd, 1, ctypes.byref(rule), 0), 'landlock_add_rule')
            finally:
                os.close(parent)
        check(LIBC.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0), 'no_new_privs')
        check(LIBC.syscall(RESTRICT_SELF, fd, 0), 'landlock_restrict_self')
    finally:
        os.close(fd)


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] != '--inside-namespace':
        # A private PID/proc namespace prevents generated code from reading
        # environment or process information from unrelated host processes.
        # No host mount is changed. Fail closed if namespaces are unavailable.
        namespace_env = {'WORKSPACE_DIR': os.environ['WORKSPACE_DIR']}
        os.execve('/usr/bin/unshare', [
            '/usr/bin/unshare', '--user', '--map-root-user', '--mount', '--pid',
            '--fork', '--mount-proc', sys.executable, __file__, '--inside-namespace',
            *sys.argv[1:],
        ], namespace_env)
    workspace = Path(os.environ['WORKSPACE_DIR']).resolve(strict=True)
    command = sys.argv[2:]
    if not command:
        raise ValueError('missing command')
    for directory in (workspace / '.tmp', workspace / '.cache', workspace / '.config'):
        directory.mkdir(exist_ok=True)
    confine(workspace)
    env = {
        'PATH': '/shared/miniconda3/bin:/usr/bin:/bin',
        'WORKSPACE_DIR': str(workspace),
        'TMPDIR': str(workspace / '.tmp'),
        'XDG_CACHE_HOME': str(workspace / '.cache'),
        'XDG_CONFIG_HOME': str(workspace / '.config'),
        'PLAYWRIGHT_BROWSERS_PATH': str(PROJECT / '.playwright-browsers'),
        'PYTHONDONTWRITEBYTECODE': '1',
        'LANG': 'C.UTF-8',
    }
    # subprocess(shell=True, executable=...) passes [-c, command].
    if command[0] == '-c':
        command = ['/bin/bash', *command]
    os.execvpe(command[0], command, env)


if __name__ == '__main__':
    main()
