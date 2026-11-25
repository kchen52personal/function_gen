"""One genuine WebWright retry using the original development task and setup.

This is an isolated diagnostic, not a held-out benchmark or synthesis input.
The agent is not told the result of the separate manually coded control.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from public_environment import IsolatedPublicEnvironment,PROJECT

HERE=Path(__file__).resolve().parent
SOURCE=PROJECT/'automated_runs/netease_spoken_16dev50held_publicget_20261118_v1'
URL='https://music.163.com/'
SESSION='/shared/hans/.secrets/browser_sessions/session_f5b252d24b8b'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--name',required=True)
    parser.add_argument('--worker',action='store_true')
    args=parser.parse_args()
    if not args.name or '/' in args.name or args.name in ('.','..'):
        raise ValueError('invalid run name')
    root=PROJECT/'automated_runs'/args.name
    if not args.worker:
        root.mkdir(parents=True,exist_ok=False)
        script=root/'source_driver.py'
        shutil.copyfile(__file__,script)
        log=root/'launcher.log'
        with log.open('x') as out:
            process=subprocess.Popen(['/usr/bin/timeout','--signal=TERM','--kill-after=30s','1950s',
                sys.executable,'-u',str(HERE/'run_webwright_playback_diagnostic.py'),
                '--name',args.name,'--worker'],stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,
                cwd=HERE,start_new_session=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
        record={'status':'launched','watchdog_pid':process.pid,'run_directory':str(root),
                'launched_at':datetime.now(timezone.utc).isoformat(),
                'driver_sha256':hashlib.sha256(script.read_bytes()).hexdigest(),
                'source_development_task':'task_001','eligible_for_pipeline_synthesis':False}
        (root/'launch.json').write_text(json.dumps(record,indent=2)+'\n')
        print(json.dumps(record),flush=True)
        return
    tasks=json.loads((SOURCE/'tasks/development/tasks.json').read_text())['tasks']
    task=next(t for t in tasks if t['task_id']=='task_001')
    contract=json.loads((SOURCE/'tasks/contract.json').read_text())
    state={'status':'running','started_at':datetime.now(timezone.utc).isoformat(),
           'source_run':str(SOURCE),'task_id':task['task_id'],'request':task['parameters']['request'],
           'eligible_for_pipeline_synthesis':False,'held_out_task':False,
           'manual_control_outcome_given_to_agent':False,'per_attempt_seconds':1800}
    (root/'summary.json').write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
    env=IsolatedPublicEnvironment(root,URL,agent_steps=60,agent_calls=80,inner_calls=12,
        allow_media_playback=True,resource_hosts=('music.126.net',),session_directory=SESSION,
        raw_request_argument=True,generation_batch_size=8,distinct_entities=True,
        parallel_generation_development=True,diagnostic_head_requests=True,diagnostic_public_get=True)
    try:
        env.run_development(URL,contract,[task],root/'development_round_00',
                            attempt_timeout=1800,queue_timeout=1840)
        data=json.loads((root/'development_round_00/summary.json').read_text())
        state.update(status='attempt_finished',attempts=data.get('attempts',[]))
    except BaseException as exc:
        state.update(status='runner_error',error_type=type(exc).__name__)
        raise
    finally:
        env.close()
        state['finished_at']=datetime.now(timezone.utc).isoformat()
        (root/'summary.json').write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
