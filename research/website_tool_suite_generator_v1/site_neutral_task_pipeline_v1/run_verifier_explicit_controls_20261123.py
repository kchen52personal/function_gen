"""Two exploratory visual controls with explicit type labels and no account data."""
from datetime import datetime, timezone
import json
from uuid import uuid4
import run_verifier_controls_20261120 as graphics
import visual_verifier as verifier

def main():
    out=graphics.ROOT/('verifier_explicit_controls_20261123_'+uuid4().hex[:8]);out.mkdir(mode=0o700)
    before=out/'before.png';wrong=out/'wrong.png';hidden=out/'hidden.png'
    graphics.make_picture(before,['Notes','No items yet'])
    graphics.make_picture(wrong,['Notes','New item','Item type: text document','Item type is not mind map'])
    graphics.make_picture(hidden,['Notes','New item','Item type: hidden from view'])
    contract={'goal':'Create a new mind map and report its type.','returns':{'selected_type':'str'}}
    task={'task_id':'synthetic','parameters':{},'intent':'Create a new mind map.'}
    answer={'parameters':{},'result':{'selected_type':'mind map'},'source_url':'https://example.org/'}
    rows=[]
    for index,(name,expected,image) in enumerate((('explicit_wrong_type','fail',wrong),('hidden_type','inconclusive',hidden))):
        report=verifier.judge('https://example.org/',contract,task,answer,[before,image],out/f'case_{index:02d}',
            model='qwen3.5-flash',timeout_seconds=75)
        rows.append({'case':name,'expected':expected,'status':report['status'],
                     'error_type':report.get('error_type'),'format':report['format']['status']})
        (out/'summary.json').write_text(json.dumps({'time':datetime.now(timezone.utc).isoformat(),
            'cases':rows,'limitations':'Exploratory synthetic controls, not a blinded reliability benchmark.',
            'held_data_opened':False},indent=2)+'\n')
        print(json.dumps(rows[-1]),flush=True)
    print('OUTPUT='+str(out),flush=True)

if __name__=='__main__': main()
