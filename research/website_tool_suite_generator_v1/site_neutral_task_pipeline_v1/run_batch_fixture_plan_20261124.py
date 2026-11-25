"""Plan (do not visually judge) an existing two-click fixture evidence bundle."""
import json
import hashlib
from pathlib import Path
from uuid import uuid4
from evidence_batch_planner import plan_batches

ROOT=Path('/shared/hans/research/website_tool_suite_generator_v1/automated_runs')
SOURCE=ROOT/'verifier_observer_isolation_20261124_bc36e2a2'


def main():
    output=ROOT/('verifier_batch_fixture_plan_20261124_'+uuid4().hex[:8]);output.mkdir(mode=0o700)
    capsule=SOURCE/'signed_capsule'
    manifest=json.loads((capsule/'manifest.json').read_text())['body']
    captures=manifest['captures']
    if [x['role'] for x in captures]!=['before','after','action','action']:
        raise ValueError('unexpected fixture capture roles')
    byte_hash_match=all(hashlib.sha256((capsule/x['name']).read_bytes()).hexdigest()==x['sha256']
                        for x in captures)
    if not byte_hash_match:raise ValueError('fixture files do not match recorded hashes')
    # Manual development checks describe what to inspect, not a gold verdict.
    evidence=[{'key':'before','role':'before'},{'key':'after','role':'after'},
              {'key':'action0','role':'action'},{'key':'action1','role':'action'}]
    checks=[{'id':'R1','requirement':'Inspect the first observed press and resulting state.',
             'evidence':['before','action0','after']},
            {'id':'R2','requirement':'Inspect the second observed press and resulting state.',
             'evidence':['action1','after']}]
    plan=plan_batches(evidence,checks)
    summary={'status':plan['status'],'source_fixture':str(SOURCE),'plan':plan,
        'source_files_match_recorded_hashes':byte_hash_match,
        'model_calls':0,'judge_result':None,'held_tasks_opened':False,
        'limitations':['Host-authored fixture task and checks, not an independent test.',
            'Evidence mappings are manual and may be incomplete or wrong.',
            'A plan does not prove screenshot authenticity or task success.',
            'The prototype capsule key was ephemeral; no later HMAC verification was performed.',
            'Recorded hash matching alone cannot prove authenticity.']}
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({'output':str(output),'status':plan['status'],'batches':len(plan['batches'])}))


if __name__=='__main__':main()
