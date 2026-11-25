"""Two-batch visual diagnostic on non-private host-authored fixture captures."""
import hashlib
import json
from pathlib import Path
from uuid import uuid4
from batch_visual_verifier import assess

ROOT=Path('/shared/hans/research/website_tool_suite_generator_v1/automated_runs')
SOURCE=ROOT/'verifier_observer_isolation_20261124_bc36e2a2'


def main():
    manifest=json.loads((SOURCE/'signed_capsule/manifest.json').read_text())['body']
    captures=manifest['captures']
    if [x['role'] for x in captures]!=['before','after','action','action']:
        raise ValueError('unexpected fixture evidence')
    for item in captures:
        if hashlib.sha256((SOURCE/'signed_capsule'/item['name']).read_bytes()).hexdigest()!=item['sha256']:
            raise ValueError('fixture evidence hash mismatch')
    events=[json.loads(x) for x in (SOURCE/'cdp_trace.jsonl').read_text().splitlines()]
    actions=[x for x in events if x.get('event')=='action_capture']
    if len(actions)!=2 or any(actions[i]['sha256']!=captures[i+2]['sha256'] for i in range(2)):
        raise ValueError('action trace does not match captured images')
    keys=['before','after','action0','action1'];evidence=[]
    for index,(key,item) in enumerate(zip(keys,captures)):
        meta={}
        if index>=2:
            event=actions[index-2]
            meta={'mouse_press_x_css':event['x'],'mouse_press_y_css':event['y'],
                  'timing':'Immediately before a mouse press; screenshot/coordinate provenance remains experimental.'}
        evidence.append({'key':key,'role':item['role'],'path':SOURCE/'signed_capsule'/item['name'],'metadata':meta})
    checks=[{'id':'R1','requirement':'Check the first press targeted the requested product add control and the result is visible.',
             'evidence':['before','action0','after']},
            {'id':'R2','requirement':'Check the second press targeted the site logo and the final page is home.',
             'evidence':['action1','after']}]
    task={'task_id':'two_action_fixture','parameters':{},
          'intent':'Add Desk Mat XL to the cart, then press the Northline logo to return home.'}
    contract={'goal':task['intent'],'returns':{'product':'str','outcome':'str'}}
    answer={'parameters':{},'result':{'product':'Desk Mat XL','outcome':'added and returned home'},
            'source_url':'https://example.org/'}
    output=ROOT/('verifier_batch_fixture_judge_20261124_'+uuid4().hex[:8])
    report=assess('https://example.org/',contract,task,answer,evidence,checks,output,
                  allow_external=True,model='qwen3.8-flash',timeout_seconds=90)
    # Separate wrapper avoids pretending that synthetic manual checks are a test score.
    note={'source_fixture':str(SOURCE),'model':'qwen3.8-flash','status':report['status'],
          'diagnostic_status':report['diagnostic_status'],'batches':report['batches'],
          'limitations':['Host-authored mock task, candidate, checks and synthetic claim.',
            'A cited screenshot can be fabricated or visually misleading.',
            'example.org is a schema-only placeholder, not a visited site.',
            'Ephemeral signing key unavailable; recorded file hashes do not prove authenticity.',
            'No held-out task, live account, execution replay or production gate was used.']}
    (output/'fixture_context.json').write_text(json.dumps(note,indent=2)+'\n')
    print(json.dumps({'output':str(output),'status':report['status'],
                      'diagnostic_status':report['diagnostic_status'],'completed_batches':len(report['batches'])}),flush=True)


if __name__=='__main__':main()
