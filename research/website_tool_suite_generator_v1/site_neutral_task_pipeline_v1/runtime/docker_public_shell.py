#!/shared/miniconda3/bin/python
"""Disposable, network-disabled browser; only per-workspace IPC is mounted."""
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

ROOT = Path('/shared/hans/research/website_tool_suite_generator_v1')
RUNTIME = Path(__file__).resolve().parent
SNAP = ROOT/'automated_runs/container_compat_20261117_v2'
IMAGE = 'sha256:a40b28c1872002f1761da1fde2f890ad509a90939136935a8ef9736e8fa17f19'
DOCKER = '/usr/bin/docker'


def stream_files(cid, base, paths):
    archive = subprocess.Popen(['/usr/bin/tar', '-C', str(base), '-cf', '-', *paths],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        extracted = subprocess.run([DOCKER, 'exec', '-i', cid, 'tar', '-xf', '-', '-C', '/tmp',
                                    '--no-same-owner', '--no-same-permissions'],
                                   stdin=archive.stdout, timeout=40, check=False)
    finally:
        archive.stdout.close()
    error = archive.stderr.read()
    if archive.wait(timeout=5) or extracted.returncode:
        raise RuntimeError('dependency copy failed: '+error.decode(errors='replace')[:300])


def main():
    workspace = Path(os.environ['WORKSPACE_DIR']).resolve(strict=True)
    if ROOT.resolve() not in workspace.parents or not workspace.is_dir():
        raise ValueError('workspace outside project')
    if not (workspace/'.egress.sock').is_socket():
        raise ValueError('host egress capability missing')
    args = sys.argv[1:]
    if not args:
        raise ValueError('missing command')
    if args[0] == '-c':
        if len(args) != 2:
            raise ValueError('unexpected shell invocation')
        args = ['/bin/bash', '-c', args[1]]
    elif args[0] in ('/shared/miniconda3/bin/python', '/shared/miniconda3/bin/python3'):
        args[0] = 'python3'
    name = 'ww-public-command-'+uuid4().hex[:16]
    login_only = os.environ.get('WW_PUBLIC_LOGIN_MODE') == '1'
    private_state = os.environ.get('WW_PRIVATE_SESSION_FILE')
    if private_state:
        # Import trusted host code, not code from the generated command workspace.
        sys.path.insert(0, str(RUNTIME.parent))
        from private_session import session_file
        selected = session_file(Path(private_state).parent, os.environ['WW_SESSION_WEBSITE'])
        if str(selected) != private_state:
            raise ValueError('unexpected private session filename')
    roomy = login_only or bool(private_state)
    cmd = [DOCKER, 'run', '--detach', '--rm', '--pull=never', '--name', name,
           '--label', f'hans.public.workspace={workspace}',
           '--network', 'none', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
           '--pids-limit', '512' if roomy else '192', '--cpus', '1', '--memory', '3072m' if roomy else '1536m', '--read-only',
           '--shm-size', '512m' if roomy else '256m', '--ulimit', 'fsize=104857600:104857600',
           '--tmpfs', '/tmp:rw,exec,nosuid,nodev,size=512m' if roomy else '/tmp:rw,exec,nosuid,nodev,size=256m', '--user', f'{os.getuid()}:{os.getgid()}',
           '--mount', f'type=bind,src={workspace},dst={workspace}', '--workdir', str(workspace),
           '--env', 'HOME=/tmp', '--env', f'WORKSPACE_DIR={workspace}',
           '--env', f'PYTHONPATH={workspace}:/tmp/deps:/tmp/vendor',
           '--env', 'PLAYWRIGHT_BROWSERS_PATH=/ms-playwright',
           '--env', f'PATH={workspace}/.container_bin:/usr/local/bin:/usr/bin:/bin',
           '--env', 'PYTHONDONTWRITEBYTECODE=1']
    if private_state:
        cmd += ['--env', 'WW_BROWSER_SESSION_STATE=/tmp/ww_private_session/state.json']
    cmd += ['--entrypoint', '/usr/bin/timeout', IMAGE, '900s', 'sleep', '900']
    try:
        cid = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=True).stdout.strip()
        if not re.fullmatch(r'[0-9a-f]{64}', cid):
            raise ValueError('unexpected container id')
        stream_files(cid, SNAP, ['deps', 'vendor'])
        stream_files(cid, RUNTIME, ['public_container_entry.py', 'public_browser_policy.py', 'private_state_writer.py'])
        if private_state:
            # stdin provisioning under the container's non-root UID needs no
            # ownership-changing capability; nothing is copied into the workspace.
            with open(private_state,'rb') as source:
                subprocess.run([DOCKER, 'exec', '-i', cid, 'python3', '/tmp/private_state_writer.py'],
                               stdin=source, timeout=10, check=True, capture_output=True)
        result = subprocess.run([DOCKER, 'exec', cid, 'python3', '/tmp/public_container_entry.py', *args],
                                timeout=850, check=False)
        return result.returncode if result.returncode >= 0 else 128-result.returncode
    except subprocess.TimeoutExpired:
        return 124
    finally:
        subprocess.run([DOCKER, 'stop', '--time', '2', name], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=8, check=False)


if __name__ == '__main__':
    raise SystemExit(main())
