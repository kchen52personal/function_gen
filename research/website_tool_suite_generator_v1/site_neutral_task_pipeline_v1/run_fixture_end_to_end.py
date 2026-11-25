"""One bounded end-to-end experiment; verifier is retrospective, never a gate."""
import hashlib
import argparse
import json
from pathlib import Path

import adaptive
from fixture_environment import IsolatedFixtureEnvironment, PROJECT, URL
import visual_verifier

ROOT=PROJECT/'automated_runs/fixture_e2e_20261118_v1'
IDEA=('Open the requested product category using the storefront, sort its products by price from '
      'lowest to highest, and return up to the requested number of products, including their '
      'names and prices. If fewer are available, return all available products. Read-only; '
      'do not change cart or check out.')


def shadow_review(root, *, judge=visual_verifier.judge, url=URL):
    """Only after generation has stopped. Judgments cannot change the pipeline summary."""
    summary=root/'summary.json'
    original=summary.read_bytes()
    state=json.loads(original)
    if state['status'].endswith('_running'):
        raise ValueError('shadow review requires pipeline to have stopped')
    target=root/'shadow_verifier';target.mkdir(exist_ok=False)
    report={'role':'retrospective diagnostic only','eligible_for_pipeline_gate':False,
            'verifier_accuracy':None,'reviews':[],
            'limitations':['No independent gold labels; judgments are not a correctness score.',
                           'At most four submitted development and four executed held cases reviewed.',
                           'Screenshots may not establish all returned values or completeness.']}
    contract=state.get('contract')
    if not contract:
        report['status']='no_contract'
    else:
        tasks={}
        for manifest in [root/'tasks/development/tasks.json',*sorted((root/'replacements').glob('round_*/tasks.json'))]:
            if manifest.is_file():
                tasks.update({t['task_id']:t for t in json.loads(manifest.read_text())['tasks']})
        cases=[]
        for row in state.get('development_attempts',[]):
            if row.get('status')!='webwright_submitted':continue
            agent=root/'development_rounds'/f"round_{row['round']:02d}"/row['task_id']/'qwen3.5-plus/agent'
            cases.append((tasks[row['task_id']],agent,'development'))
            if len(cases)==4:break
        # Held inputs are read here only if the frozen pipeline already opened them.
        if state.get('held_opened'):
            held={t['task_id']:t for t in json.loads((root/'tasks/held_out/tasks.json').read_text())['tasks']}
            for checkpoint in state.get('checkpoints',[])[:1]:
                for row in checkpoint.get('held_results',[])[:4]:
                    workspace=root/'checkpoints'/f"checkpoint_{checkpoint['development_submission_count']:02d}"/'held_checks'/row['task_id']/'workspace'
                    cases.append((held[row['task_id']],workspace,'held'))
        for task,workspace,split in cases:
            row={'task_id':task['task_id'],'split':split}
            logs=sorted(workspace.rglob('final_script_log.txt'),key=lambda p:p.stat().st_mtime)
            if not logs:
                row.update(status='inconclusive',reason='No final result log; no model call.')
            else:
                try:
                    result,legacy=visual_verifier.parse_result_log(logs[-1].read_text())
                    images=sorted(logs[-1].parent.rglob('*.png'))[:3]
                    verdict=judge(url,contract,task,result,images,target/f'{split}_{task["task_id"]}',
                                  model='qwen3.5-flash',timeout_seconds=90,legacy_marker=legacy)
                    row.update(status=verdict['status'],format=verdict.get('format'),
                               reason=verdict.get('reason'))
                except Exception as exc:
                    row.update(status='inconclusive',reason='Shadow review failed: '+type(exc).__name__)
            report['reviews'].append(row)
            (target/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
        report['status']='complete_diagnostic_only'
    report['pipeline_summary_unchanged']=summary.read_bytes()==original
    report['pipeline_summary_sha256']=hashlib.sha256(original).hexdigest()
    (target/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    if not report['pipeline_summary_unchanged']:
        raise RuntimeError('pipeline summary unexpectedly changed during shadow review')
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-name',default=ROOT.name)
    parser.add_argument('--idea',default=IDEA)
    parser.add_argument('--allow-fixture-cart',action='store_true')
    parser.add_argument('--experimental-visual-verification',action='store_true',
                        help='Run the experimental two-stage visual gate on the disposable fixture')
    parser.add_argument('--development-count',type=int,default=8,choices=range(2,33))
    parser.add_argument('--held-count',type=int,default=4,choices=range(1,101))
    parser.add_argument('--overall-seconds',type=int,default=21600,choices=range(600,21601))
    args=parser.parse_args()
    if not args.run_name or '/' in args.run_name or args.run_name in ('.','..'):
        parser.error('run name must be a simple directory name')
    root=PROJECT/'automated_runs'/args.run_name
    if root.exists():raise FileExistsError('Refusing to overwrite an experiment')
    try:
        with IsolatedFixtureEnvironment(root,allow_cart=args.allow_fixture_cart) as environment:
            state=adaptive.execute(URL,args.idea,root,max_rounds=3,target=args.development_count,overall_seconds=args.overall_seconds,
                                   final_only=True,webwright_only=True,verify_development=False,
                                   environment=environment,development_count=args.development_count,
                                   held_count=args.held_count,visual_verification=args.experimental_visual_verification)
        print(json.dumps({'status':state['status'],'submitted':state.get('submitted_development_count',0),
                          'held_opened':state['held_opened']}),flush=True)
    finally:
        if not args.experimental_visual_verification and (root/'summary.json').is_file():
            shadow_review(root)


if __name__=='__main__':main()
