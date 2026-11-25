#!/shared/miniconda3/bin/python
"""Test-only shell: run one command in a constrained disposable container.

Only an explicitly labelled private fixture container may provide networking.
No host filesystem is mounted except the agent workspace and fixed snapshots.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

ROOT=Path('/shared/hans/research/website_tool_suite_generator_v1')
SNAP=ROOT/'automated_runs/container_compat_20261117_v2'
IMAGE='sha256:a40b28c1872002f1761da1fde2f890ad509a90939136935a8ef9736e8fa17f19'
DOCKER='/usr/bin/docker'


def command_budget(value='110'):
    """Host-selected bounded command lifetime; legacy default is unchanged."""
    seconds=int(value)
    if seconds not in (110,900):raise ValueError('unsupported fixture command budget')
    return seconds


def main():
    seconds=command_budget(os.environ.get('FIXTURE_COMMAND_SECONDS','110'))
    workspace=Path(os.environ['WORKSPACE_DIR']).resolve(strict=True)
    if ROOT.resolve() not in workspace.parents or not workspace.is_dir():
        raise ValueError('agent workspace must be a project directory')
    site_id=os.environ['FIXTURE_CONTAINER_ID']
    nonce=os.environ['FIXTURE_SESSION_NONCE']
    if not re.fullmatch(r'[0-9a-f]{64}',site_id) or not re.fullmatch(r'[0-9a-f]{32}',nonce):
        raise ValueError('invalid fixture container capability')
    inspect=subprocess.run([DOCKER,'inspect',site_id],capture_output=True,text=True,timeout=10,check=True)
    item=json.loads(inspect.stdout)[0]
    if (not item['State']['Running'] or item['HostConfig']['NetworkMode']!='none'
        or item['Config']['Labels'].get('hans.fixture.nonce')!=nonce):
        raise ValueError('fixture container not isolated or capability mismatch')
    for sub in ('deps','vendor'):
        if not (SNAP/sub).is_dir() or (SNAP/sub).is_symlink(): raise ValueError('snapshot missing')
    command=sys.argv[1:]
    if not command: raise ValueError('missing command')
    if command[0]=='-c':
        if len(command)!=2: raise ValueError('unexpected shell invocation')
        args=['/bin/bash','-c',command[1]]
    else:
        if command[0] in ('/shared/miniconda3/bin/python','/shared/miniconda3/bin/python3'):
            command[0]='python3'
        args=command
    name='ww-fixture-command-'+uuid4().hex[:16]
    cmd=[DOCKER,'run','--detach','--rm','--pull=never','--name',name,'--network',f'container:{site_id}',
         '--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','192',
         '--cpus','1','--memory','1536m','--read-only','--shm-size','256m',
         '--ulimit','fsize=104857600:104857600',
         '--tmpfs','/tmp:rw,exec,nosuid,nodev,size=256m','--user',f'{os.getuid()}:{os.getgid()}',
         '--mount',f'type=bind,src={workspace},dst={workspace}',
         '--workdir',str(workspace),'--env','HOME=/tmp','--env',f'WORKSPACE_DIR={workspace}',
         '--env',f'PYTHONPATH={workspace}:/tmp/deps:/tmp/vendor','--env','PLAYWRIGHT_BROWSERS_PATH=/ms-playwright',
         '--env',f'PATH={workspace}/.container_bin:/usr/local/bin:/usr/bin:/bin',
         '--env','PYTHONDONTWRITEBYTECODE=1','--entrypoint','/usr/bin/timeout',IMAGE,f'{seconds}s','sleep',str(seconds)]
    try:
        # Dependencies are copied into the container's private tmpfs, not
        # mounted from the host: bind-mounted trees could expose host sockets.
        created=subprocess.run(cmd,capture_output=True,text=True,timeout=15,check=True)
        cid=created.stdout.strip()
        if not re.fullmatch(r'[0-9a-f]{64}',cid): raise ValueError('unexpected command container id')
        archive=subprocess.Popen(['/usr/bin/tar','-C',str(SNAP),'-cf','-','deps','vendor'],
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            extracted=subprocess.run([DOCKER,'exec','-i',cid,'tar','-xf','-','-C','/tmp',
                                      '--no-same-owner','--no-same-permissions'],
                                     stdin=archive.stdout,stdout=subprocess.DEVNULL,timeout=40,check=False)
        finally:
            archive.stdout.close()
        archive_error=archive.stderr.read()
        archive_code=archive.wait(timeout=5)
        if archive_code or extracted.returncode:
            raise RuntimeError('dependency extraction failed: '+archive_error.decode(errors='replace')[:300])
        run=subprocess.run([DOCKER,'exec',cid,*args],timeout=seconds-5,check=False)
        raise SystemExit(run.returncode if run.returncode>=0 else 128-run.returncode)
    except subprocess.TimeoutExpired:
        print('fixture command timed out',file=sys.stderr)
        raise SystemExit(124)
    finally:
        # Randomized name belongs only to this invocation; --rm removes it on stop.
        subprocess.run([DOCKER,'stop','--time','2',name],stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,timeout=8,check=False)


if __name__=='__main__': main()
