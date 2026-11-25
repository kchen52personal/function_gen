"""Fresh exploratory event-search controls after clarification of missing evidence."""
from datetime import datetime, timezone
import json
from uuid import uuid4
import run_verifier_controls_20261120 as graphics
import visual_verifier as verifier

def main():
    out=graphics.ROOT/('verifier_event_controls_20261123_'+uuid4().hex[:8]);out.mkdir(mode=0o700)
    hidden=out/'hidden.png';wrong=out/'wrong.png'
    graphics.make_picture(hidden,['Events','Evening concert','Date: 12 May','Venue: not shown on this screen'])
    graphics.make_picture(wrong,['Events','Evening concert','Date: 12 May','Venue: River Hall'])
    contract={'goal':'Find the event and report its venue.','returns':{'venue':'str'}}
    task={'task_id':'synthetic','parameters':{},'intent':'Find the venue of the Evening concert on 12 May.'}
    answer={'parameters':{},'result':{'venue':'Oak Theatre'},'source_url':'https://example.org/'}
    rows=[]
    for index,(name,expected,image) in enumerate((('hidden_venue','inconclusive',hidden),('wrong_venue','fail',wrong))):
        report=verifier.judge('https://example.org/',contract,task,answer,[image],out/f'case_{index:02d}',
            model='qwen3.5-flash',timeout_seconds=75)
        rows.append({'case':name,'expected':expected,'status':report['status'],
                     'error_type':report.get('error_type'),'format':report['format']['status']})
        (out/'summary.json').write_text(json.dumps({'time':datetime.now(timezone.utc).isoformat(),
            'cases':rows,'limitations':'Fresh but hand-authored exploratory cases, not a blinded benchmark.',
            'held_data_opened':False},indent=2)+'\n')
        print(json.dumps(rows[-1]),flush=True)
    print('OUTPUT='+str(out),flush=True)

if __name__=='__main__': main()
