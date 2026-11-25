"""Fresh examples for distinguishing current state from complete history."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4
import run_verifier_controls_20261120 as graphics
import visual_verifier as v

def main():
    out=graphics.ROOT/('verifier_history_controls_20261123_'+uuid4().hex[:8]);out.mkdir(mode=0o700)
    cases=[('negative_history','inconclusive','Confirm that no message was ever sent.',
            ['Messaging','Sent folder: empty'],'No message was ever sent'),
           ('current_state','pass','Report whether the sent folder is currently empty.',
            ['Messaging','Sent folder: empty'],'The sent folder is currently empty')]
    rows=[];digest=hashlib.sha256(Path(v.__file__).read_bytes()).hexdigest()
    for i,(name,expected,intent,lines,answer) in enumerate(cases):
        image=out/f'image_{i:02d}.png';graphics.make_picture(image,lines)
        task={'task_id':f'case_{i:02d}','parameters':{},'intent':intent}
        contract={'goal':intent,'returns':{'answer':'str'}}
        result={'parameters':{},'result':{'answer':answer},'source_url':'https://example.org/'}
        report=v.judge('https://example.org/',contract,task,result,[image],out/f'case_{i:02d}',
                       model='qwen3.5-flash',timeout_seconds=75)
        rows.append({'case':name,'expected':expected,'status':report['status'],
                     'guard':report.get('history_guard'),'error_type':report.get('error_type'),
                     'elapsed_seconds':report.get('elapsed_seconds')})
        (out/'summary.json').write_text(json.dumps({'time':datetime.now(timezone.utc).isoformat(),
           'verifier_sha256':digest,'cases':rows,'held_data_opened':False,
           'limits':'Hand-authored exploratory cases; no real-world reliability inference.'},indent=2)+'\n')
        print(json.dumps(rows[-1]),flush=True)
    print('OUTPUT='+str(out),flush=True)

if __name__=='__main__':main()
