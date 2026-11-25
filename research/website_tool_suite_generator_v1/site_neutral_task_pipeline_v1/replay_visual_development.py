"""Review one existing development artifact; never read a held manifest."""
import argparse
import json
from pathlib import Path
import re
import visual_verifier as verifier
from codex_artifact_guard import checked_file

RUN_ROOT=Path('/shared/hans/research/website_tool_suite_generator_v1/automated_runs').resolve()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('source_run'); p.add_argument('task_id'); p.add_argument('final_run')
    p.add_argument('--model',default='qwen3.5-flash',choices=['qwen3.5-flash','qwen3.5-plus','qwen3.8-flash'])
    p.add_argument('--timeout-seconds',type=int,default=120)
    p.add_argument('--output',required=True)
    args=p.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_-]+',args.task_id) or not re.fullmatch(r'run_\d+',args.final_run):
        raise ValueError('invalid artifact identifier')
    root=Path(args.source_run).resolve(strict=True)
    out=Path(args.output).resolve()
    if RUN_ROOT not in root.parents or RUN_ROOT not in out.parents:
        raise ValueError('run and output must be inside the automated-runs directory')
    summary=json.loads(checked_file(root,root/'summary.json').read_text())
    tasks=json.loads(checked_file(root,root/'tasks/development/tasks.json').read_text())['tasks']
    task=next((x for x in tasks if x['task_id']==args.task_id),None)
    if task is None: raise ValueError('task is not in the development split')
    saved=(root/'development_rounds/round_00'/args.task_id/'qwen3.5-plus/agent/final_runs'/args.final_run).resolve(strict=True)
    if root not in saved.parents: raise ValueError('artifact escapes source run')
    result,legacy=verifier.parse_result_log(checked_file(saved,saved/'final_script_log.txt').read_text())
    images=[checked_file(saved,saved/'screenshots/before.png'),checked_file(saved,saved/'screenshots/after.png')]
    answer=verifier.judge(summary['website'],summary['contract'],task,result,images,out,
                          legacy_marker=legacy,model=args.model,timeout_seconds=args.timeout_seconds)
    print(json.dumps({'task_id':args.task_id,'visual_status':answer['status'],
                      'format_status':answer['format']['status'],'error_type':answer.get('error_type'),
                      'held_opened':False}))

if __name__=='__main__': main()
