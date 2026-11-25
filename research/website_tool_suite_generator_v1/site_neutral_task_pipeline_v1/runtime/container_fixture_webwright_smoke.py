"""One bounded WebWright smoke in the container-backed local fixture mode."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from uuid import uuid4
import yaml

PROJECT=Path('/shared/hans/research/website_tool_suite_generator_v1')
RUNTIME=Path(__file__).resolve().parent
PIPELINE=PROJECT/'pipeline_codex_v73'
sys.path[:0]=[str(RUNTIME),str(PIPELINE)]
from base_config import build_config
from codex_bounded_process import run_bounded
from tool_broker import Broker

ROOT=PROJECT/'automated_runs/container_fixture_webwright_smoke_20261117_v1'
WORKSPACE=PROJECT/'ct_webwright_20261117v1'
FIXTURE=PROJECT/'automated_runs/container_isolation_20261117_v1/fixture'
IMAGE='sha256:a40b28c1872002f1761da1fde2f890ad509a90939136935a8ef9736e8fa17f19'
URL='http://127.0.0.1:18765/'
TASK=('On this local demonstration storefront, find the Aluminum Laptop Stand '
      'and report its displayed price. Save a screenshot as evidence and '
      'submit the reusable browser script. Do not purchase anything.')


def write(value):
    tmp=ROOT/'.summary.tmp'; tmp.write_text(json.dumps(value,indent=2)+'\n')
    os.replace(tmp,ROOT/'summary.json')


def main():
    global ROOT, WORKSPACE
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-name', default='container_fixture_webwright_smoke_20261117_v1')
    parser.add_argument('--workspace-name', default='ct_webwright_20261117v1')
    parser.add_argument('--agent-calls', type=int, default=6, choices=range(1, 41))
    parser.add_argument('--inner-calls', type=int, default=2, choices=range(1, 9))
    parser.add_argument('--wall-seconds', type=int, default=900, choices=range(60, 1801))
    parser.add_argument('--command-seconds', type=int, default=110, choices=(110,900))
    parser.add_argument('--task-file', type=Path, help='JSON with task, success_criteria, and requested_constraints')
    parser.add_argument('--allow-fixture-cart', action='store_true', help='allow cart changes only in the isolated demo store')
    parser.add_argument('--fixture-version',choices=('legacy','cart_v2'),default='legacy')
    args=parser.parse_args()
    from fixture_session import FIXTURES
    fixture=FIXTURES[args.fixture_version]
    if any('/' in name or name in ('.','..') for name in (args.run_name,args.workspace_name)):
        parser.error('names must be simple directory names')
    ROOT=PROJECT/'automated_runs'/args.run_name
    WORKSPACE=PROJECT/args.workspace_name
    spec={'task':TASK, 'success_criteria':['Report the product price visible on the site.'],
          'requested_constraints':['No purchases or real personal information.']}
    if args.task_file:
        spec=json.loads(args.task_file.read_text())
        if (not isinstance(spec,dict) or not isinstance(spec.get('task'),str)
            or not spec['task'].strip() or len(spec['task'])>8000
            or any(not isinstance(spec.get(k),list) or not spec[k]
                   or any(not isinstance(v,str) or not v.strip() for v in spec[k])
                   for k in ('success_criteria','requested_constraints'))):
            parser.error('invalid task specification')
    ROOT.mkdir(parents=True,exist_ok=False); WORKSPACE.mkdir(exist_ok=False)
    for name in ('.tmp','.cache','.config'): (ROOT/name).mkdir()
    (WORKSPACE/'.container_bin').mkdir(); (WORKSPACE/'.container_bin/python').symlink_to('/usr/bin/python3')
    shutil.copyfile(RUNTIME/'broker_tool_model.py',WORKSPACE/'broker_tool_model.py')
    (WORKSPACE/'tool_model.yaml').write_text(yaml.safe_dump({'model':{
        'model_class':'broker_tool_model.BrokerToolModel','model_name':'qwen3.5-plus',
        'request_timeout_seconds':180}}))
    state={'status':'starting','started_at':datetime.now(timezone.utc).isoformat(),
        'purpose':'local-fixture WebWright integration smoke; not a benchmark score','task':spec['task'],
        'model':'qwen3.5-plus','workspace':str(WORKSPACE),'image':IMAGE,
        'agent_call_cap':args.agent_calls,'inner_call_cap':args.inner_calls,'wall_timeout_seconds':args.wall_seconds,
        'fixture_cart_allowed':args.allow_fixture_cart,'command_timeout_seconds':args.command_seconds,
        'fixture_version':args.fixture_version,
        'limitations':['Single development task; no held-out accuracy is measured.',
          'Submission is not independent proof of correctness.',
          'The container boundary passed limited checks but is not a complete security audit.',
          'Browser image uses Playwright 1.49.1 rather than the host runtime version.',
          'A new browser container is started for each WebWright command; browser state resets.',
          'The agent workspace is writable and does not have a total disk quota.']}
    write(state)
    nonce=uuid4().hex; site_name='ww-fixture-run-'+nonce[:12]; site_id=None
    started=time.monotonic()
    try:
        cmd=['/usr/bin/docker','run','--detach','--rm','--pull=never','--name',site_name,
          '--label',f'hans.fixture.nonce={nonce}','--network','none','--cap-drop','ALL',
          '--security-opt','no-new-privileges','--pids-limit','64','--cpus','1','--memory','256m',
          '--read-only','--tmpfs','/tmp:rw,nosuid,nodev,size=64m','--user',f'{os.getuid()}:{os.getgid()}',
          '--mount',f'type=bind,src={fixture},dst=/fixture,readonly',
          '--entrypoint','/usr/bin/timeout',IMAGE,f'{args.wall_seconds + 200}s','python3','/fixture/serve_fixture.py','--port','18765']
        site=subprocess.run(cmd,capture_output=True,text=True,timeout=20,check=True); site_id=site.stdout.strip()
        env={**os.environ,'PYTHONPATH':f'{PIPELINE}:{PROJECT / "vendor/Webwright/src"}:{RUNTIME}',
            'FIXTURE_CONTAINER_ID':site_id,'FIXTURE_SESSION_NONCE':nonce,
            'FIXTURE_COMMAND_SECONDS':str(args.command_seconds),
            'XDG_CONFIG_HOME':str(ROOT/'.config'),'XDG_CACHE_HOME':str(ROOT/'.cache'),
            'TMPDIR':str(ROOT/'.tmp'),'PYTHONDONTWRITEBYTECODE':'1'}
        config=build_config({'success_criteria':spec['success_criteria'],
                             'requested_constraints':spec['requested_constraints']},ROOT)
        config['model']['model_name']='qwen3.5-plus'; config['model']['max_api_calls']=args.agent_calls
        config['agent']['step_limit']=args.agent_calls
        config['agent']['system_template']=config['agent']['system_template'].replace('six-call cap',f'{args.inner_calls}-call cap')
        if args.allow_fixture_cart:
            template=config['agent']['system_template']
            old='Never log in, purchase, add to a cart, or change account/site state.'
            if old not in template:
                raise ValueError('expected local fixture safety policy missing')
            config['agent']['system_template']=template.replace(old,
                'Never log in, purchase, check out, or enter real personal information. '
                'Cart changes are allowed only on this isolated local demo.').replace(
                'this is a public read-only task.', 'this is an isolated local demonstration task.')
            config['agent']['system_template'] += ('\nComplete cart actions through user-facing browser controls. '
                'DOM inspection for extraction is allowed, but do not write browser storage, mutate app/DOM '
                'state directly, invoke private application stores, intercept responses, or fabricate page '
                'evidence. A cart toast is not proof: inspect actual cart lines and quantities.\n')
        config['agent']['system_template'] += ('\nWhen a locator fails, inspect the actual nearby DOM and accessible '
            'names, confirm the locator uniquely matches the intended control, and retry based on that evidence. '
            'Do not substitute a memorized or hard-coded answer for data that the page should supply. '
            'A click or typed input is not proof that an action succeeded: inspect the resulting page state, '
            'including any error or unavailable message, and report failure honestly if the requested state is absent.\n')
        config['environment']['shell']=str(RUNTIME/'docker_fixture_shell.py')
        config['environment']['command_timeout_seconds']=args.command_seconds+60
        config['environment']['env'].update(FIXTURE_CONTAINER_ID=site_id,FIXTURE_SESSION_NONCE=nonce,
                                           FIXTURE_COMMAND_SECONDS=str(args.command_seconds))
        (ROOT/'config.yaml').write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False))
        agent_cmd=[sys.executable,str(RUNTIME/'launch_agent_v3.py'),'--task',spec['task'],'--task-id',
            'container_fixture_smoke','--workspace',str(WORKSPACE),'--config',str(ROOT/'config.yaml'),
            '--start-url',URL]
        state.update(status='running',site_container_id=site_id); write(state)
        with Broker(WORKSPACE,ROOT,limit=args.inner_calls,model='qwen3.5-plus',pathname_socket=True) as broker:
            code,stdout,stderr=run_bounded(agent_cmd,cwd=PROJECT,env=env,timeout=args.wall_seconds,
                cancel_requested=broker.budget_exhausted.is_set,cancel_reason='inner model budget exhausted')
            state['inner_calls']=broker.count
        (ROOT/'stdout.log').write_text(stdout); (ROOT/'stderr.log').write_text(stderr)
        info={}; trajectory=WORKSPACE/'trajectory.json'
        if trajectory.is_file(): info=json.loads(trajectory.read_text()).get('info',{})
        script=WORKSPACE/'final_script.py'
        state.update(status='finished',process_exit=code,trajectory_status=info.get('exit_status'),
            agent_calls=info.get('api_calls'),final_script_present=script.is_file() and not script.is_symlink(),
            semantic_correctness='not_independently_verified')
    except Exception as exc:
        state.update(status='infrastructure_error',error_type=type(exc).__name__,error=str(exc)[:1200])
    finally:
        if site_id:
            try:
                stopped=subprocess.run(['/usr/bin/docker','stop','--time','3',site_id],capture_output=True,
                                       text=True,timeout=12,check=False)
                state['site_stop_returncode']=stopped.returncode
            except Exception as exc: state['site_stop_error']=type(exc).__name__
        state['elapsed_seconds']=round(time.monotonic()-started,2)
        state['finished_at']=datetime.now(timezone.utc).isoformat(); write(state)


if __name__=='__main__': main()
