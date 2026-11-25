"""Two read-only visual checks of one public saved DEVELOPMENT trajectory.

The changed answer is an intentionally corrupted control, not an agent output.
The original has not been independently fully verified and is not gold-labeled.
"""
import copy
import argparse
import json
from pathlib import Path
from uuid import uuid4
import visual_verifier as v

BASE=Path('/shared/hans/research/website_tool_suite_generator_v1/automated_runs')
SOURCE=BASE/'arxiv_8dev_webwright_only_20261116_v1'

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--after-only',action='store_true')
    parser.add_argument('--timeout',type=int,default=90,choices=range(10,121))
    parser.add_argument('--model',choices=['qwen3.5-flash','qwen3.8-flash'],default='qwen3.5-flash')
    args=parser.parse_args()
    summary=json.loads((SOURCE/'summary.json').read_text())
    tasks=json.loads((SOURCE/'tasks/development/tasks.json').read_text())['tasks']
    task=next(t for t in tasks if t['task_id']=='task_003')
    saved=SOURCE/'development_rounds/round_00/task_003/qwen3.5-plus/agent/final_runs/run_001'
    result,legacy=v.parse_result_log((saved/'final_script_log.txt').read_text())
    corrupted=copy.deepcopy(result)
    corrupted['result']['items'][0]['title']='Completely Different Invented Title'
    images=([saved/'screenshots/after.png'] if args.after_only else
            [saved/'screenshots/before.png',saved/'screenshots/after.png'])
    out=BASE/('verifier_saved_arxiv_corruption_20261123_'+uuid4().hex[:8]);out.mkdir(mode=0o700)
    rows=[]
    for i,(name,answer) in enumerate((('original_unlabeled',result),('corrupted_title',corrupted))):
        report=v.judge(summary['website'],summary['contract'],task,answer,images,out/f'case_{i:02d}',
                       model=args.model,timeout_seconds=args.timeout,legacy_marker=legacy)
        rows.append({'case':name,'status':report['status'],'error_type':report.get('error_type'),
                     'format':report['format']['status'],'elapsed_seconds':report.get('elapsed_seconds')})
        (out/'summary.json').write_text(json.dumps({'cases':rows,'source_split':'development',
            'original_is_gold':False,'held_tasks_opened':False,'after_only':args.after_only,'model':args.model,
            'limitations':['Old screenshot/script provenance unverified.',
                           'Corruption control is synthetic; no general accuracy claim.']},indent=2)+'\n')
        print(json.dumps(rows[-1]),flush=True)
    print('OUTPUT='+str(out),flush=True)

if __name__=='__main__': main()
