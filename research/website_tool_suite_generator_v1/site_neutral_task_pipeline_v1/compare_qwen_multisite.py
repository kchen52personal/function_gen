#!/usr/bin/env python3
"""Counterbalanced, development-only WebWright submission/timing comparison.

This is NOT a correctness benchmark. No held-out files or independent semantic
verifier are used; outputs and self-reflection evidence remain available for a
separate post-hoc audit.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import time

import adaptive
from api_timing import summarize
import development
from process_identity import current_identity

MODELS = ('qwen3.8-flash', 'qwen3.5-plus')
TASK_INDICES = (1, 5)


def plan_sources(sources):
    if len(sources)!=2:
        raise ValueError('expected exactly two source runs')
    plan=[]
    for source in sources:
        state=json.loads((source/'summary.json').read_text())
        manifest=source/'tasks/development/tasks.json'
        if adaptive.digest(manifest)!=state['split_hashes']['development']:
            raise ValueError('development manifest hash mismatch')
        tasks=json.loads(manifest.read_text())['tasks']
        if len(tasks)!=8: raise ValueError('expected eight development cases')
        contract=json.loads((source/'tasks/contract.json').read_text())
        for index in TASK_INDICES:
            plan.append({'source':str(source),'website':state['website'],'task':tasks[index],
                         'development_sha256':adaptive.digest(manifest), 'contract':contract,
                         'contract_sha256':adaptive.digest(source/'tasks/contract.json')})
    if plan[0]['website']==plan[2]['website']:
        raise ValueError('expected two distinct websites')
    return plan


def execute(sources, output, *, attempt_seconds=1800, overall_seconds=16200):
    sources=[Path(x).resolve(strict=True) for x in sources]
    output=Path(output).resolve()
    if any(output.is_relative_to(source) for source in sources):
        raise ValueError('output must be separate from source runs')
    plan=plan_sources(sources)
    output.mkdir(parents=True,exist_ok=False)
    for name in ('.config','.cache','.tmp'): (output/name).mkdir()
    os.environ.update(XDG_CONFIG_HOME=str(output/'.config'),XDG_CACHE_HOME=str(output/'.cache'),
                      TMPDIR=str(output/'.tmp'),PLAYWRIGHT_BROWSERS_PATH=str(development.PROJECT/'.playwright-browsers'))
    state={'status':'running', 'started_at':datetime.now(timezone.utc).isoformat(),
           'process_identity':current_identity(),
           'models':list(MODELS), 'task_selection':'indices 1 and 5 from each of two development manifests, fixed in advance',
           'task_plan':[{'website':x['website'],'source':x['source'],'task_id':x['task']['task_id'],
                         'development_sha256':x['development_sha256'],'contract_sha256':x['contract_sha256']} for x in plan],
           'held_out_opened':False, 'independent_correctness_verifier':False, 'attempts':[],
           'limits':{'seconds_per_attempt':attempt_seconds,'overall_seconds':overall_seconds,
                     'agent_calls_per_attempt':50,'inner_calls_per_attempt':12},
           'limitations':['WebWright submission is not a correctness score.',
             'Four paired development tasks across two sites are an exploratory sample, not a model ranking.',
             'Both the outer agent and its inner tools change model; this is a whole-system comparison.',
             'Live website changes and concurrent API traffic may confound sequential timings.',
             'Completed API-call durations can overlap and miss interrupted requests.',
             'Network access is not hard restricted to the target host or read-only.']}
    def save(): adaptive.save(output/'summary.json',state)
    save()
    started=time.monotonic()
    try:
        for index, case in enumerate(plan):
            for model in (MODELS if index%2==0 else tuple(reversed(MODELS))):
                remaining=int(overall_seconds-(time.monotonic()-started))
                if remaining<=0:
                    state.update(status='stopped_overall_budget',finished_at=datetime.now(timezone.utc).isoformat())
                    state.pop('current',None);save();return state
                source=Path(case['source'])
                if adaptive.digest(source/'tasks/development/tasks.json')!=case['development_sha256'] or adaptive.digest(source/'tasks/contract.json')!=case['contract_sha256']:
                    raise ValueError('source input changed during comparison')
                task=case['task']
                state['current']={'case_index':index,'task_id':task['task_id'],'website':case['website'],
                                  'model':model,'started_at':datetime.now(timezone.utc).isoformat()}
                save()
                root=output/f'case_{index+1:02d}'/model/'run'
                root.parent.mkdir(parents=True,exist_ok=True)
                start=time.monotonic();run_error=None
                try:
                    development.run(case['website'],case['contract'],[task],root,
                        attempt_timeout=min(attempt_seconds,remaining),
                        queue_timeout=min(attempt_seconds+40,remaining),model=model)
                except Exception as exc:
                    run_error=type(exc).__name__
                try:
                    batch=json.loads((root/'summary.json').read_text())
                except (OSError,ValueError,TypeError): batch={}
                item=next((x for x in batch.get('attempts',[]) if x.get('task_id')==task['task_id']),{})
                script,origin=adaptive.select_development_candidate(root,task,item,model=model)
                submitted=origin=='submitted' and script is not None
                stats_root=root/task['task_id']/model/'api_logs/stats'
                state['attempts'].append({'case_index':index,'website':case['website'],'task_id':task['task_id'],
                    'model':model,'trajectory_status':item.get('trajectory_status'),'process_exit':item.get('process_exit'),
                    'submitted_and_hash_checked':submitted,'candidate_origin':origin,
                    'development_wall_seconds':round(time.monotonic()-start,2),
                    'agent_calls':item.get('agent_calls'),'inner_tool_calls':item.get('inner_tool_calls'),
                    'inner_tool_budget_exhausted':item.get('inner_tool_budget_exhausted'),
                    'site_access_failure_signal':item.get('site_access_failure_signal',False),
                    'run_error_type':run_error,'correctness':'not_assessed',
                    'observed_stats_model_directories':sorted(x.name for x in stats_root.iterdir() if x.is_dir()) if stats_root.exists() else [],
                    'api_timing':summarize(stats_root)})
                state.pop('current',None);save()
                if batch.get('status')=='stopped_repeated_site_access_failures':
                    state.update(status='stopped_site_access_failure',finished_at=datetime.now(timezone.utc).isoformat())
                    save();return state
        state.update(status='complete_submission_and_timing_only',finished_at=datetime.now(timezone.utc).isoformat());save()
        return state
    except Exception as exc:
        state.update(status='stopped_error',error_type=type(exc).__name__,error_message=str(exc)[:300],
                     finished_at=datetime.now(timezone.utc).isoformat());save();raise


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('source_a',type=Path);parser.add_argument('source_b',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();execute([args.source_a,args.source_b],args.output)


if __name__=='__main__':
    def interrupted(signum,frame): raise TimeoutError(f'comparison received signal {signum}')
    signal.signal(signal.SIGTERM,interrupted)
    main()
