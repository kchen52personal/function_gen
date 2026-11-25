"""Synthetic state-change controls, separate from the real account and held tasks."""
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4
import run_verifier_controls_20261120 as graphics
import visual_verifier as verifier


def main():
    out=graphics.ROOT/('verifier_state_controls_20261123_'+uuid4().hex[:8])
    out.mkdir(mode=0o700)
    before=out/'before.png'; right=out/'after_right.png'; wrong=out/'after_wrong.png'; missing=out/'after_missing.png'
    graphics.make_picture(before,['Notes application','My documents','Existing note: Shopping list'])
    graphics.make_picture(right,['Notes application','My documents','Existing note: Shopping list','New blank mind map'])
    graphics.make_picture(wrong,['Notes application','My documents','Existing note: Shopping list','New blank document'])
    graphics.make_picture(missing,['Notes application','My documents','Note created successfully','Document type not visible'])
    contract={'goal':'Create a new blank mind map and report its type.',
              'returns':{'selected_type':'str'}}
    task={'task_id':'synthetic','parameters':{},'intent':'Create a new blank mind map.'}
    answer={'parameters':{},'result':{'selected_type':'mind map'},'source_url':'https://example.org/'}
    cases=[('correct','pass',right),('wrong_type','fail',wrong),('type_not_visible','inconclusive',missing)]
    rows=[]; consecutive_errors=0
    for n,(name,expected,after) in enumerate(cases):
        if consecutive_errors >= 2:
            rows.append({'case':name,'expected':expected,'status':'not_run_api_failures'})
        else:
            report=verifier.judge('https://example.org/',contract,task,answer,[before,after],out/f'case_{n:02d}',
                                  model='qwen3.5-flash',timeout_seconds=90)
            consecutive_errors=consecutive_errors+1 if report.get('error_type') else 0
            rows.append({'case':name,'expected':expected,'status':report['status'],
                         'format':report['format']['status'],'error_type':report.get('error_type')})
        (out/'summary.json').write_text(json.dumps({'time':datetime.now(timezone.utc).isoformat(),
             'cases':rows,'limits':'Synthetic exploratory controls; not a blinded benchmark.',
             'held_data_opened':False},indent=2)+'\n')
        print(json.dumps(rows[-1]),flush=True)
    print('OUTPUT='+str(out),flush=True)

if __name__=='__main__': main()
