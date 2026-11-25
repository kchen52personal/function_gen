"""Host-owned bounded detached launcher; never reads session contents or held data."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from public_environment import HERE, PROJECT, IsolatedPublicEnvironment
from factory import validate_site


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('configuration')
    args=parser.parse_args()
    config=json.loads(Path(args.configuration).read_text())
    name=config['run_name']
    if not name or '/' in name or name in ('.','..'):
        raise ValueError('invalid run name')
    validate_site(config['website'],config['functionality'])
    overall=config.get('overall_seconds',21600)
    if type(overall) is not int or not 600<=overall<=43200:
        raise ValueError('invalid bounded overall runtime')
    watchdog=overall+1800
    root=PROJECT/'automated_runs'/name
    log=PROJECT/'automated_runs'/(name+'_launcher.log')
    record=PROJECT/'automated_runs'/(name+'_launch.json')
    sources=PROJECT/'automated_runs'/(name+'_source_snapshot')
    if any(p.exists() for p in (root,log,record,sources)):
        raise FileExistsError('refusing to overwrite a run or launch artifact')
    # Validate the saved baseline before starting a process. No secret is emitted.
    IsolatedPublicEnvironment(root,config['website'],session_directory=config.get('session_directory'),
                              development_workers=config.get('development_workers',7))
    sources.mkdir()
    hashes={}
    for source in [*HERE.glob('*.py'),*(HERE/'runtime').glob('*.py')]:
        target=sources/source.relative_to(HERE)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
        hashes[str(source.relative_to(HERE))]=hashlib.sha256(target.read_bytes()).hexdigest()
    # The environment class is loaded from the sibling pipeline at runtime;
    # retain its exact source alongside the site-neutral files for provenance.
    for name in ('webwright_safe_environment.py','webwright_yibu_model.py','local_propose.py'):
        source=PROJECT/'pipeline_codex_v73'/name
        target=sources/'external/pipeline_codex_v73'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
        hashes[f'external/pipeline_codex_v73/{name}']=hashlib.sha256(target.read_bytes()).hexdigest()
    (sources/'sha256.json').write_text(json.dumps(hashes,indent=2)+'\n')
    command=['/usr/bin/timeout','--signal=TERM','--kill-after=30s',str(watchdog)+'s',
             sys.executable,'-u',str(HERE/'run_public_end_to_end.py'),
             config['website'],config['functionality'],'--run-name',name,
             '--held-count',str(config.get('held_count',4)),
             '--development-count',str(config.get('development_count',8)),
             '--agent-steps',str(config.get('agent_steps',40)),
             '--agent-calls',str(config.get('agent_calls',40)),
             '--inner-calls',str(config.get('inner_calls',6)),
             '--development-workers',str(config.get('development_workers',7)),
             '--generation-batch-size',str(config.get('generation_batch_size',0)),
             '--max-replacement-rounds',str(config.get('max_replacement_rounds',3)),
             '--overall-seconds',str(overall)]
    if config.get('allow_media_playback'):command.append('--allow-media-playback')
    if config.get('allow_authorized_creation'):command.append('--allow-authorized-creation')
    if config.get('raw_request'):command.append('--raw-request')
    if config.get('distinct_entities'):command.append('--distinct-entities')
    if config.get('parallel_generation_development'):command.append('--parallel-generation-development')
    if config.get('diagnostic_head_requests'):command.append('--diagnostic-head-requests')
    if config.get('diagnostic_public_get'):command.append('--diagnostic-public-get')
    for host in config.get('resource_hosts',[]):command+=['--resource-host',host]
    if config.get('session_directory'):command+=['--session-directory',config['session_directory']]
    if config.get('generation_resume_checkpoint'):
        command+=['--generation-resume-checkpoint',config['generation_resume_checkpoint']]
    with log.open('x') as output:
        process=subprocess.Popen(command,cwd=HERE,stdin=subprocess.DEVNULL,stdout=output,
            stderr=subprocess.STDOUT,start_new_session=True,
            env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
    info={'status':'launched','watchdog_pid':process.pid,'run_directory':str(root),
          'log':str(log),'source_snapshot':str(sources),
          'launched_at':datetime.now(timezone.utc).isoformat(),
          'watchdog_seconds':watchdog,'pipeline_seconds':overall,
          'authenticated_baseline':bool(config.get('session_directory')),
          'development_count':config.get('development_count',8),
          'development_workers':config.get('development_workers',7),
          'distinct_entities':bool(config.get('distinct_entities')),
          'parallel_generation_development':bool(config.get('parallel_generation_development')),
          'diagnostic_head_requests':bool(config.get('diagnostic_head_requests')),
          'diagnostic_public_get':bool(config.get('diagnostic_public_get')),
          'held_count':config.get('held_count',4),'raw_request':bool(config.get('raw_request'))}
    info['allow_authorized_creation']=bool(config.get('allow_authorized_creation'))
    record.write_text(json.dumps(info,indent=2)+'\n')
    print(json.dumps(info),flush=True)


if __name__=='__main__':main()
