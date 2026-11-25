"""Two-container, no-model security and browser probe; removes only its own containers."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
from uuid import uuid4

ROOT=Path('/shared/hans/research/website_tool_suite_generator_v1')
SOURCE=ROOT/'local_fixtures/ecommerce_storefront_20261117'
OUT=ROOT/'automated_runs/container_isolation_20261117_v1'
AGENT=ROOT/'ct_agent_20261117v1'
RUNTIME=Path(__file__).resolve().parent
IMAGE='sha256:a40b28c1872002f1761da1fde2f890ad509a90939136935a8ef9736e8fa17f19'
DOCKER='/usr/bin/docker'


def invoke(args, timeout=30):
    return subprocess.run([DOCKER,*args],capture_output=True,text=True,timeout=timeout,check=False)


def main():
    OUT.mkdir(parents=True,exist_ok=False); AGENT.mkdir(exist_ok=False)
    fixture=OUT/'fixture'; fixture.mkdir()
    for name in ('dist','offline_images_20261117_v1'):
        shutil.copytree(SOURCE/name,fixture/name,symlinks=False)
    shutil.copyfile(SOURCE/'serve_fixture.py',fixture/'serve_fixture.py')
    shutil.copyfile(RUNTIME/'container_agent_probe.py',AGENT/'probe.py')
    tag=uuid4().hex[:12]; site_name='ww-fixture-site-'+tag; agent_name='ww-fixture-probe-'+tag
    common=['--pull=never','--cap-drop','ALL','--security-opt','no-new-privileges',
            '--pids-limit','192','--cpus','1','--read-only','--tmpfs','/tmp:rw,nosuid,nodev,size=256m',
            '--user',f'{os.getuid()}:{os.getgid()}','--env','HOME=/tmp','--shm-size','256m']
    result={'status':'running','image':IMAGE,'agent_workspace':str(AGENT),
            'agent_data':'only known smoke task; no held data or secrets',
            'limits':'separate disposable containers; no GPU; no model calls'}
    server_id=None
    with tempfile.TemporaryDirectory(prefix='.hostsock_',dir='/shared/hans') as outside:
        bad=Path(outside)/'other.sock'
        broker=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        host=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        host_tcp=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        broker.bind(str(AGENT/'broker.sock')); os.chmod(AGENT/'broker.sock',0o600); broker.listen(1); broker.settimeout(80)
        host.bind(str(bad)); host.listen(1); host.settimeout(80)
        host_tcp.bind(('127.0.0.1',0)); host_tcp.listen(1); port=host_tcp.getsockname()[1]
        accepted=[]
        def handle(sock,label):
            try:
                with sock.accept()[0] as connection:
                    message=connection.recv(32); accepted.append(label)
                    connection.sendall(b'fake-model' if message==b'probe' else b'bad')
            except OSError: pass
        for sock,label in ((broker,'broker'),(host,'host')):
            threading.Thread(target=handle,args=(sock,label),daemon=True).start()
        try:
            site=invoke(['run','--detach','--rm','--name',site_name,'--network','none',*common,
                '--memory','256m','--mount',f'type=bind,src={fixture},dst=/fixture,readonly',
                '--entrypoint','python3',IMAGE,'/fixture/serve_fixture.py','--port','18765'])
            if site.returncode != 0:
                raise RuntimeError('site container start failed: '+site.stderr[:1000])
            server_id=site.stdout.strip()
            if len(server_id)!=64 or any(c not in '0123456789abcdef' for c in server_id):
                raise RuntimeError('unexpected container id')
            agent=invoke(['run','--rm','--name',agent_name,'--network',f'container:{server_id}',*common,
                '--memory','1536m','--mount',f'type=bind,src={AGENT},dst=/agent',
                '--env','PLAYWRIGHT_BROWSERS_PATH=/ms-playwright','--entrypoint','python3',IMAGE,
                '/agent/probe.py',str(bad),str(port)],timeout=90)
            result.update(agent_exit=agent.returncode,agent_stderr=agent.stderr[-3000:])
            try: result['checks']=json.loads(agent.stdout)
            except ValueError: result['agent_stdout']=agent.stdout[-3000:]
            result['host_accepts']=list(accepted)
            checks=result.get('checks',{}); browser=checks.get('browser',{})
            result['passed']=(agent.returncode==0 and checks.get('fake_broker')=='connected:fake-model'
                and not str(checks.get('unrelated_host_socket','connected')).startswith('connected')
                and not str(checks.get('docker_socket','connected')).startswith('connected') and checks.get('host_loopback')!='connected'
                and checks.get('external_network')!='connected' and checks.get('host_file_visible') is False
                and checks.get('fixture_source_visible') is False and checks.get('effective_caps')=='0000000000000000'
                and checks.get('root_write')!='allowed' and browser.get('status')==200
                and browser.get('price_visible') is True and browser.get('broken_images')==0
                and not browser.get('nonlocal_requests') and accepted==['broker'])
            result['status']='completed'
        except Exception as exc:
            result.update(status='error',error_type=type(exc).__name__,error=str(exc)[:1500],passed=False)
        finally:
            # These randomized names were created only for this probe; no other containers are touched.
            agent_stop=invoke(['stop','--time','3',agent_name],timeout=12)
            if server_id:
                site_stop=invoke(['stop','--time','3',server_id],timeout=12)
                result['site_stop_returncode']=site_stop.returncode
            broker.close(); host.close(); host_tcp.close()
    # Closing a Unix socket does not delete its filesystem node; it is not result data.
    socket_path=AGENT/'broker.sock'
    if socket_path.exists() and socket_path.is_socket(): socket_path.unlink()
    (OUT/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    if not result.get('passed'): raise SystemExit(1)


if __name__=='__main__': main()
