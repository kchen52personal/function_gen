"""Replay a saved WebWright reflection with a bounded inner-call budget."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
from uuid import uuid4

PROJECT = Path('/shared/hans/research/website_tool_suite_generator_v1')
RUNTIME = Path(__file__).resolve().parent
sys.path[:0]=[str(RUNTIME),str(PROJECT/'pipeline_codex_v73')]
from tool_broker import Broker

RUN = PROJECT / 'automated_runs/fixture_reflection_replay_20261117_v1'
WORKSPACE = PROJECT / 'ct_fixture_reflection_20261117v1'
SOURCE = PROJECT / 'ct_fixture_sort_20261117v2'
FIXTURE = PROJECT / 'automated_runs/container_isolation_20261117_v1/fixture'
IMAGE = 'sha256:a40b28c1872002f1761da1fde2f890ad509a90939136935a8ef9736e8fa17f19'


def main():
    RUN.mkdir(parents=True,exist_ok=False)
    WORKSPACE.mkdir(exist_ok=False)
    (WORKSPACE/'.container_bin').mkdir()
    (WORKSPACE/'.container_bin/python').symlink_to('/usr/bin/python3')
    for name in ('self_reflect_config.json','tool_model.yaml','broker_tool_model.py'):
        shutil.copyfile(SOURCE/name,WORKSPACE/name)
    shutil.copytree(SOURCE/'final_runs/run_002',WORKSPACE/'final_runs/run_002')
    nonce=uuid4().hex
    name='ww-fixture-replay-'+nonce[:12]
    site_id=None
    state={'status':'starting','inner_call_cap':6,'wall_seconds':125,
           'purpose':'self-check plumbing on saved development evidence; not an accuracy test'}
    started=time.monotonic()
    try:
        cmd=['/usr/bin/docker','run','--detach','--rm','--pull=never','--name',name,
             '--label',f'hans.fixture.nonce={nonce}','--network','none','--cap-drop','ALL',
             '--security-opt','no-new-privileges','--pids-limit','64','--cpus','1','--memory','256m',
             '--read-only','--tmpfs','/tmp:rw,nosuid,nodev,size=64m','--user',f'{os.getuid()}:{os.getgid()}',
             '--mount',f'type=bind,src={FIXTURE},dst=/fixture,readonly','--entrypoint','/usr/bin/timeout',
             IMAGE,'800s','python3','/fixture/serve_fixture.py','--port','18765']
        site_id=subprocess.run(cmd,capture_output=True,text=True,timeout=20,check=True).stdout.strip()
        env={**os.environ,'WORKSPACE_DIR':str(WORKSPACE),'FIXTURE_CONTAINER_ID':site_id,
             'FIXTURE_SESSION_NONCE':nonce}
        inner=['python','-m','cuawright.webwright.tools.self_reflection','--config','self_reflect_config.json',
               '--workspace-dir',str(WORKSPACE),'--model-config',str(WORKSPACE/'tool_model.yaml'),
               '--output','final_runs/run_002/reflection_replay.json']
        with Broker(WORKSPACE,RUN,limit=6,model='qwen3.5-plus',pathname_socket=True) as broker:
            result=subprocess.run([str(RUNTIME/'docker_fixture_shell.py'),'-c',shlex.join(inner)],
                                  env=env,capture_output=True,text=True,timeout=125,check=False)
            state.update(status='finished',process_exit=result.returncode,inner_calls=broker.count,
                         inner_budget_exhausted=broker.budget_exhausted.is_set())
        (RUN/'stdout.log').write_text(result.stdout)
        (RUN/'stderr.log').write_text(result.stderr)
        report=WORKSPACE/'final_runs/run_002/reflection_replay.json'
        if report.exists():
            data=json.loads(report.read_text())
            state['predicted_label']=data.get('predicted_label')
    except Exception as exc:
        state.update(status='error',error_type=type(exc).__name__,error=str(exc)[:300])
    finally:
        if site_id:
            stopped=subprocess.run(['/usr/bin/docker','stop','--time','3',site_id],capture_output=True,
                                   text=True,timeout=12,check=False)
            state['site_stop_returncode']=stopped.returncode
        state['elapsed_seconds']=round(time.monotonic()-started,2)
        (RUN/'summary.json').write_text(json.dumps(state,indent=2)+'\n')


if __name__=='__main__':
    main()
