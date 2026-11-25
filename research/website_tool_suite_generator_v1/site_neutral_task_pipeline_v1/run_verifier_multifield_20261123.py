"""Exploratory multi-requirement omission controls on synthetic page images."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4
import run_verifier_controls_20261120 as graphics
import visual_verifier as v

def main():
    out=graphics.ROOT/('verifier_multifield_20261123_'+uuid4().hex[:8]);out.mkdir(mode=0o700)
    cases=[
      ('calendar_wrong_room','fail','Report the event date, time, and room.',
       ['Event','Date: May 12','Time: 09:30','Room: Birch'],
       {'date':'May 12','time':'09:30','room':'Oak'}),
      ('stock_wrong_state','fail','Report the product name, price, and stock status.',
       ['Product','Name: Lamp','Price: $18','Stock: Out of stock'],
       {'name':'Lamp','price':'$18','stock':'In stock'}),
      ('shipping_correct','pass','Report the order number, status, and carrier.',
       ['Order details','Order: 382','Status: Shipped','Carrier: North Post'],
       {'order':'382','status':'Shipped','carrier':'North Post'}),
      ('contact_hidden_field','inconclusive','Report the contact name, email, and city.',
       ['Contact','Name: Mira','Email: mira@example.org','City: not shown'],
       {'name':'Mira','email':'mira@example.org','city':'Boston'}),
    ]
    rows=[];failures=0;digest=hashlib.sha256(Path(v.__file__).read_bytes()).hexdigest()
    for i,(name,expected,intent,lines,fields) in enumerate(cases):
        if failures>=2:
            rows.append({'case':name,'expected':expected,'status':'not_run_api_failures'})
        else:
            image=out/f'image_{i:02d}.png';graphics.make_picture(image,lines)
            task={'task_id':f'case_{i:02d}','parameters':{},'intent':intent}
            contract={'goal':intent,'returns':{key:'str' for key in fields}}
            result={'parameters':{},'result':fields,'source_url':'https://example.org/'}
            report=v.judge('https://example.org/',contract,task,result,[image],out/f'case_{i:02d}',
                           model='qwen3.5-flash',timeout_seconds=75)
            failures=failures+1 if report.get('error_type') else 0
            rows.append({'case':name,'expected':expected,'status':report['status'],
                         'error_type':report.get('error_type'),'elapsed_seconds':report.get('elapsed_seconds')})
        (out/'summary.json').write_text(json.dumps({'time':datetime.now(timezone.utc).isoformat(),
            'verifier_sha256':digest,'cases':rows,'held_data_opened':False,
            'limits':'Hand-authored synthetic development checks, not a blinded generalization benchmark.'},indent=2)+'\n')
        print(json.dumps(rows[-1]),flush=True)
    print('OUTPUT='+str(out),flush=True)

if __name__=='__main__':main()
