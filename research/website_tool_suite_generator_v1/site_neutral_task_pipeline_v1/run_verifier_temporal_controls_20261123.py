"""Fresh exploratory before/after checks; role labels are supplied by the test harness."""
from datetime import datetime, timezone
import json
from uuid import uuid4
import run_verifier_controls_20261120 as graphics
import visual_verifier as verifier

def main():
    out=graphics.ROOT/('verifier_temporal_controls_20261123_'+uuid4().hex[:8]);out.mkdir(mode=0o700)
    before=out/'before.png';graphics.make_picture(before,['Recipe planner','Servings: 4'])
    cases=[('after_hidden','inconclusive',['Recipe planner','Servings: temporarily unavailable'],'8'),
           ('after_correct','pass',['Recipe planner','Servings: 8'],'8'),
           ('after_wrong','fail',['Recipe planner','Servings: 6'],'8'),
           ('reported_old_value','fail',['Recipe planner','Servings: 8'],'4')]
    rows=[];errors=0
    for i,(name,expected,lines,answer_text) in enumerate(cases):
        if errors>=2:
            rows.append({'case':name,'expected':expected,'status':'not_run_api_failures'})
        else:
            after=out/f'after_{i:02d}.png';graphics.make_picture(after,lines)
            task={'task_id':f'case_{i:02d}','parameters':{},'intent':'Change servings to 8 and report the resulting value.'}
            contract={'goal':task['intent'],'returns':{'answer':'str'}}
            result={'parameters':{},'result':{'answer':answer_text},'source_url':'https://example.org/'}
            report=verifier.judge('https://example.org/',contract,task,result,[before,after],out/f'case_{i:02d}',
                                  model='qwen3.5-flash',timeout_seconds=75,image_roles=['before','after'])
            errors=errors+1 if report.get('error_type') else 0
            rows.append({'case':name,'expected':expected,'status':report['status'],
                         'error_type':report.get('error_type'),'elapsed_seconds':report.get('elapsed_seconds')})
        (out/'summary.json').write_text(json.dumps({'time':datetime.now(timezone.utc).isoformat(),
            'cases':rows,'limits':'Hand-authored synthetic development controls, not a blinded reliability benchmark.',
            'held_data_opened':False},indent=2)+'\n')
        print(json.dumps(rows[-1]),flush=True)
    print('OUTPUT='+str(out),flush=True)

if __name__=='__main__':main()
