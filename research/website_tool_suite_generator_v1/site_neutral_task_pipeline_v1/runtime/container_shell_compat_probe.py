"""Test the container shell, browser and real reflection CLI with a fake broker."""
import json
import os
from pathlib import Path
import shutil
import shlex
import socket
import subprocess
import sys
import tempfile
from uuid import uuid4
import yaml

ROOT=Path('/shared/hans/research/website_tool_suite_generator_v1')
RUNTIME=Path(__file__).resolve().parent
sys.path[:0]=[str(RUNTIME),str(ROOT/'pipeline_codex_v73')]
import tool_broker

OUT=ROOT/'automated_runs/container_shell_compat_20261117_v5'
WORKSPACE=ROOT/'ct_agent_20261117v6'
FIXTURE=ROOT/'automated_runs/container_isolation_20261117_v1/fixture'
IMAGE='sha256:a40b28c1872002f1761da1fde2f890ad509a90939136935a8ef9736e8fa17f19'
DOCKER='/usr/bin/docker'


def docker(args, timeout=20):
    return subprocess.run([DOCKER,*args],capture_output=True,text=True,timeout=timeout,check=False)


def main():
    OUT.mkdir(parents=True,exist_ok=False); WORKSPACE.mkdir(exist_ok=False)
    (WORKSPACE/'.container_bin').mkdir()
    (WORKSPACE/'.container_bin/python').symlink_to('/usr/bin/python3')
    shutil.copyfile(RUNTIME/'broker_tool_model.py',WORKSPACE/'broker_tool_model.py')
    shutil.copyfile(RUNTIME/'container_shell_browser_probe.py',WORKSPACE/'browser_probe.py')
    shutil.copyfile(RUNTIME/'container_shell_security_probe.py',WORKSPACE/'security_probe.py')
    (WORKSPACE/'tool_model.yaml').write_text(yaml.safe_dump({'model':{
        'model_class':'broker_tool_model.BrokerToolModel','model_name':'qwen3.5-plus'}}))
    nonce=uuid4().hex; name='ww-fixture-compat-'+nonce[:12]
    result={'status':'running','model_calls':'none; fake broker only','runs':[]}
    site_id=None
    try:
        site=docker(['run','--detach','--rm','--pull=never','--name',name,'--label',f'hans.fixture.nonce={nonce}',
            '--network','none','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','64',
            '--cpus','1','--memory','256m','--read-only','--tmpfs','/tmp:rw,nosuid,nodev,size=64m',
            '--user',f'{os.getuid()}:{os.getgid()}','--mount',f'type=bind,src={FIXTURE},dst=/fixture,readonly',
            '--entrypoint','/usr/bin/timeout',IMAGE,'180s','python3','/fixture/serve_fixture.py','--port','18765'])
        if site.returncode: raise RuntimeError('fixture server failed: '+site.stderr[:800])
        site_id=site.stdout.strip()
        env={**os.environ,'WORKSPACE_DIR':str(WORKSPACE),'FIXTURE_CONTAINER_ID':site_id,
             'FIXTURE_SESSION_NONCE':nonce}
        tool_broker._logger=lambda _:None
        count=[]
        with tool_broker.Broker(WORKSPACE,OUT,limit=3,pathname_socket=True) as broker:
            def fake(request):
                count.append(1); return 'Score: 5\nReasoning: fake test response.\nStatus: success'
            broker.call=fake
            shell=str(RUNTIME/'docker_fixture_shell.py')
            def run(label,command,environment=env,expect=0):
                proc=subprocess.run([shell,'-c',command],env=environment,capture_output=True,text=True,timeout=125,check=False)
                (OUT/(label+'.stdout.txt')).write_text(proc.stdout)
                (OUT/(label+'.stderr.txt')).write_text(proc.stderr)
                result['runs'].append({'label':label,'exit':proc.returncode})
                if (proc.returncode==0)!=(expect==0): raise RuntimeError(label+' unexpected exit: '+proc.stderr[-900:])
                return proc
            run('browser','python browser_probe.py')
            with tempfile.TemporaryDirectory(prefix='.ct_host_',dir='/shared/hans') as host_directory:
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as host_unix, \
                     socket.socket(socket.AF_INET,socket.SOCK_STREAM) as host_tcp:
                    host_path=Path(host_directory)/'other.sock'
                    host_unix.bind(str(host_path)); host_unix.listen(1)
                    host_tcp.bind(('127.0.0.1',0)); host_tcp.listen(1)
                    cmd=f'python security_probe.py {shlex.quote(str(host_path))} {host_tcp.getsockname()[1]}'
                    security=json.loads(run('security',cmd).stdout)
            wrong={**env,'FIXTURE_SESSION_NONCE':'0'*32}
            run('invalid_capability','true',environment=wrong,expect=1)
            result['security_checks']=security
            if (any(security.get(k)=='connected' for k in ('host_unix','docker_socket','host_tcp','external_tcp'))
                or security.get('host_file_visible') is not False or security.get('fixture_source_visible') is not False
                or security.get('effective_caps')!='0000000000000000' or security.get('root_write')=='allowed'):
                raise RuntimeError('security negative controls failed')
            reflection={'images':['browser.png'],'image_judge_system_prompt':'Check the image.',
                'image_judge_user_prompt':'Describe the image.','final_verdict_system_prompt':'Return a status.',
                'final_verdict_user_prompt':'Review {image_reasonings}.'}
            (WORKSPACE/'reflection.json').write_text(json.dumps(reflection))
            run('reflection',f'python -m cuawright.webwright.tools.self_reflection '
                f'--config {WORKSPACE}/reflection.json --workspace-dir {WORKSPACE} '
                f'--model-config {WORKSPACE}/tool_model.yaml --output {WORKSPACE}/reflection_output.json')
            verdict=json.loads((WORKSPACE/'reflection_output.json').read_text())
            result.update(fake_broker_calls=len(count),reflection_label=verdict.get('predicted_label'),
                screenshot_exists=(WORKSPACE/'browser.png').is_file())
            result['passed']=(len(count)>=2 and result['reflection_label']==1 and result['screenshot_exists'])
        result['status']='completed'
    except Exception as exc:
        result.update(status='error',error_type=type(exc).__name__,error=str(exc)[:1200],passed=False)
    finally:
        if site_id:
            stopped=docker(['stop','--time','3',site_id],timeout=12)
            result['site_stop_returncode']=stopped.returncode
    (OUT/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    if not result.get('passed'): raise SystemExit(1)


if __name__=='__main__': main()
