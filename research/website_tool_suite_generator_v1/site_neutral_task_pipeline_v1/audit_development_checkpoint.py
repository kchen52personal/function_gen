#!/usr/bin/env python3
"""Reassess one saved development candidate without modifying its original run."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

import adaptive
from evaluate import evaluate_case
from factory import PROJECT

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source_run',type=Path)
    parser.add_argument('task_id')
    parser.add_argument('output',type=Path)
    args = parser.parse_args()
    source = args.source_run.resolve()
    source_summary = json.loads((source/'summary.json').read_text())
    manifest = source/'tasks/development/tasks.json'
    if adaptive.digest(manifest) != source_summary['split_hashes']['development']:
        raise ValueError('development manifest hash mismatch')
    tasks = json.loads(manifest.read_text())['tasks']
    task = next((x for x in tasks if x['task_id']==args.task_id),None)
    if task is None: raise ValueError('task is not in the development manifest')
    round_root = source/'development_rounds/round_00'
    records = json.loads((round_root/'summary.json').read_text())['attempts']
    record = next((x for x in records if x['task_id']==args.task_id),None)
    if record is None: raise ValueError('task attempt has not finished')
    script, origin = adaptive.select_development_candidate(round_root,task,record)
    if script is None: raise ValueError('no safe candidate available')
    out = args.output.resolve()
    out.mkdir(parents=True,exist_ok=False)
    for name in ('.config','.cache','.tmp'): (out/name).mkdir()
    os.environ.update(XDG_CONFIG_HOME=str(out/'.config'),XDG_CACHE_HOME=str(out/'.cache'),
        TMPDIR=str(out/'.tmp'),PLAYWRIGHT_BROWSERS_PATH=str(PROJECT/'.playwright-browsers'))
    state={'status':'assessment_running','task_id':args.task_id,'source_run':str(source),
        'source_process_exit':record.get('process_exit'),'source_trajectory_status':record.get('trajectory_status'),
        'candidate_origin':origin,'candidate_sha256':adaptive.digest(script),'held_out_opened':False,
        'started_at':datetime.now(timezone.utc).isoformat(),
        'limitation':'A smoke-valid draft and model agreement are not proof of correctness.'}
    adaptive.save(out/'summary.json',state)
    try:
        state['assessment']=evaluate_case(source_summary['website'],source_summary['contract'],task,script,out/'assessment')
        state.update(status='complete',finished_at=datetime.now(timezone.utc).isoformat())
    except Exception as exc:
        state.update(status='stopped_error',error_type=type(exc).__name__,error_message=str(exc)[:500])
    adaptive.save(out/'summary.json',state)

if __name__=='__main__': main()
