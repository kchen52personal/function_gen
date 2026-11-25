#!/shared/miniconda3/bin/python
"""Experimental test-only command sandbox with a private local fixture server.

This does not connect the model broker or enable localhost in the public
pipeline. It starts a fresh site per command in a private network namespace.
"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import time

PROJECT = Path('/shared/hans/research/website_tool_suite_generator_v1')
FIXTURES = PROJECT / 'local_fixtures'
SANDBOX = Path(__file__).resolve().with_name('sandbox_v3.py')
PYTHON = '/shared/miniconda3/bin/python'
PORT = 18765


def validated_fixture(value: str) -> Path:
    root = Path(value).resolve(strict=True)
    if FIXTURES.resolve() not in root.parents:
        raise ValueError('fixture must be inside the local_fixtures directory')
    entry = root / 'serve_fixture.py'
    if not entry.is_file() or entry.is_symlink():
        raise ValueError('fixture has no trusted server entry point')
    return root


def main() -> None:
    # Fail closed: network namespaces and Landlock do not restrict access to
    # filesystem Unix sockets. Do not remove this guard without a tested fix.
    raise RuntimeError('fixture sandbox disabled: host Unix socket isolation failed')
    inside = len(sys.argv) > 1 and sys.argv[1] == '--inside-fixture-namespace'
    fixture = validated_fixture(os.environ['FIXTURE_ROOT'])
    workspace = Path(os.environ['WORKSPACE_DIR']).resolve(strict=True)
    if PROJECT.resolve() not in workspace.parents or fixture == workspace or fixture in workspace.parents:
        raise ValueError('agent workspace must be separate from the fixture')
    command = sys.argv[2:] if inside else sys.argv[1:]
    if not command:
        raise ValueError('missing command')
    if not inside:
        env = {'WORKSPACE_DIR': str(workspace), 'FIXTURE_ROOT': str(fixture)}
        os.execve('/usr/bin/unshare', ['/usr/bin/unshare', '--user', '--map-root-user', '--net',
            '--mount', '--pid', '--fork', '--mount-proc', PYTHON, __file__,
            '--inside-fixture-namespace', *command], env)

    subprocess.run(['/usr/sbin/ip', 'link', 'set', 'lo', 'up'], check=True, timeout=5)
    fixture_env = {'WORKSPACE_DIR': str(fixture)}
    site = subprocess.Popen([PYTHON, str(SANDBOX), '--inside-namespace', PYTHON,
        str(fixture / 'serve_fixture.py'), '--port', str(PORT)], env=fixture_env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while True:
            if site.poll() is not None:
                raise RuntimeError('fixture server exited before starting')
            try:
                with socket.create_connection(('127.0.0.1', PORT), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('fixture server did not start')
                time.sleep(0.1)
        proc = subprocess.run([PYTHON, str(SANDBOX), '--inside-namespace', *command],
                              env={'WORKSPACE_DIR': str(workspace)}, check=False)
        code = proc.returncode if proc.returncode >= 0 else 128 - proc.returncode
    finally:
        site.terminate()
        try:
            site.wait(timeout=3)
        except subprocess.TimeoutExpired:
            site.kill()
            site.wait(timeout=3)
    raise SystemExit(code)


if __name__ == '__main__':
    main()
