"""Host-owned lifecycle for the fixed, offline local fixture."""
import os
from pathlib import Path
import re
import subprocess
from uuid import uuid4

PROJECT=Path('/shared/hans/research/website_tool_suite_generator_v1')
FIXTURE=PROJECT/'automated_runs/container_isolation_20261117_v1/fixture'
FIXTURES={'legacy':FIXTURE,'cart_v2':PROJECT/'local_fixtures/northline_cart_20261118_v2'}
IMAGE='sha256:a40b28c1872002f1761da1fde2f890ad509a90939136935a8ef9736e8fa17f19'
SHELL=Path(__file__).with_name('docker_fixture_shell.py')


class FixtureSession:
    def __init__(self,version='legacy'):
        if version not in FIXTURES:raise ValueError('unknown fixed fixture version')
        self.fixture=FIXTURES[version]

    def __enter__(self):
        self.nonce=uuid4().hex
        self.name='ww-fixture-benchmark-'+self.nonce[:12]
        cmd=['/usr/bin/docker','run','--detach','--rm','--pull=never','--name',self.name,
             '--label',f'hans.fixture.nonce={self.nonce}','--network','none','--cap-drop','ALL',
             '--security-opt','no-new-privileges','--pids-limit','64','--cpus','1','--memory','256m',
             '--read-only','--tmpfs','/tmp:rw,nosuid,nodev,size=64m','--user',f'{os.getuid()}:{os.getgid()}',
             '--mount',f'type=bind,src={self.fixture},dst=/fixture,readonly','--entrypoint','/usr/bin/timeout',
             IMAGE,'22000s','python3','/fixture/serve_fixture.py','--port','18765']
        self.container=subprocess.run(cmd,capture_output=True,text=True,timeout=20,check=True).stdout.strip()
        if not re.fullmatch(r'[0-9a-f]{64}',self.container): raise ValueError('invalid container ID')
        return self

    def run(self,workspace,args):
        workspace=workspace.resolve(strict=True)
        if PROJECT not in workspace.parents: raise ValueError('workspace must be in the project')
        (workspace/'.container_bin').mkdir(exist_ok=True)
        link=workspace/'.container_bin/python'
        if not link.exists(): link.symlink_to('/usr/bin/python3')
        env={**os.environ,'WORKSPACE_DIR':str(workspace),'FIXTURE_CONTAINER_ID':self.container,
             'FIXTURE_SESSION_NONCE':self.nonce}
        return subprocess.run([str(SHELL),*args],env=env,capture_output=True,text=True,timeout=140,check=False)

    def __exit__(self,*exc):
        subprocess.run(['/usr/bin/docker','stop','--time','3',self.container],capture_output=True,
                       text=True,timeout=12,check=False)
