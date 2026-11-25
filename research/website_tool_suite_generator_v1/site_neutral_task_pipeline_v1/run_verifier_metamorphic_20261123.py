"""Same-evidence answer mutation study; labels are never sent to the VLM."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from random import Random
from uuid import uuid4
import run_verifier_controls_20261120 as graphics
import visual_verifier as v


def main():
    out=graphics.ROOT/('verifier_metamorphic_20261123_'+uuid4().hex[:8]);out.mkdir(mode=0o700)
    specs=[
        ('mail','Report the subject of the selected message.', ['Inbox','Selected message','Subject: Project update'], 'Project update','Budget review'),
        ('calendar','Report the start time of the selected appointment.', ['Calendar','Appointment: Dentist','Start time: 09:30'], '09:30','10:30'),
        ('notes','Report the title of the currently open note.', ['Notes','Open note title: Meeting Notes'], 'Meeting Notes','Shopping List'),
        ('delivery','Report the status of order 384.', ['Orders','Order 384','Status: In transit'], 'In transit','Delivered'),
    ]
    plan=[]
    for i,(name,intent,lines,right,wrong) in enumerate(specs):
        image=out/f'page_{i:02d}.png';graphics.make_picture(image,lines)
        for kind,expected,answer in [('correct','pass',right),('corrupted','fail',wrong)]:
            plan.append((name,kind,expected,intent,image,answer))
    Random(887331).shuffle(plan)
    rows=[];failures=0
    digest=hashlib.sha256(Path(v.__file__).read_bytes()).hexdigest()
    for index,(domain,kind,expected,intent,image,value) in enumerate(plan):
        if failures>=3:
            rows.append({'domain':domain,'kind':kind,'expected':expected,'status':'not_run_api_failures'})
        else:
            task={'task_id':f'case_{index:02d}','parameters':{},'intent':intent}
            contract={'goal':intent,'returns':{'answer':'str'}}
            answer={'parameters':{},'result':{'answer':value},'source_url':'https://example.org/'}
            report=v.judge('https://example.org/',contract,task,answer,[image],out/f'case_{index:02d}',
                           model='qwen3.5-flash',timeout_seconds=60)
            failures=failures+1 if report.get('error_type') else 0
            rows.append({'domain':domain,'kind':kind,'expected':expected,'status':report['status'],
                         'error_type':report.get('error_type'),'elapsed_seconds':report.get('elapsed_seconds')})
        (out/'summary.json').write_text(json.dumps({'time':datetime.now(timezone.utc).isoformat(),
            'verifier_sha256':digest,'cases':rows,'held_data_opened':False,
            'limits':'Hand-authored synthetic development controls; not independent real-world accuracy.'},indent=2)+'\n')
        print(json.dumps(rows[-1]),flush=True)
    print('OUTPUT='+str(out),flush=True)

if __name__=='__main__':main()
