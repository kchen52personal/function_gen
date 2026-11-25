"""Fresh synthetic controls after the exact checklist-text mapping change."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4
import run_verifier_controls_20261120 as graphics
import coverage_verifier as cv
import visual_verifier as v


def main():
    out=graphics.ROOT/('verifier_coverage_transfer_c_20261123_'+uuid4().hex[:8]);out.mkdir(mode=0o700)
    visual_sha=hashlib.sha256(Path(v.__file__).read_bytes()).hexdigest()
    planner_sha=hashlib.sha256(Path(cv.__file__).read_bytes()).hexdigest()
    cases=[
      ('museum_correct','pass',['Museum booking','Exhibit: Clay','Day: Friday','Entry: 14:00'],
       'Report the exhibit, day, and entry time.',{'exhibit':'Clay','day':'Friday','entry':'14:00'}),
      ('rental_wrong_day','fail',['Rental details','Vehicle: Blue car','Pickup: Monday','Office: West'],
       'Report the vehicle, pickup day, and office.',{'vehicle':'Blue car','pickup':'Tuesday','office':'West'}),
      ('garden_hidden','inconclusive',['Garden order','Plant: Fern','Quantity: 2','Delivery: not shown'],
       'Report the plant, quantity, and delivery date.',{'plant':'Fern','quantity':'2','delivery':'15 April'}),
    ]
    rows=[];errors=0
    for i,(name,expected,lines,intent,fields) in enumerate(cases):
        if errors>=2:
            rows.append({'case':name,'expected':expected,'status':'not_run_api_failures'})
        else:
            image=out/f'image_{i:02d}.png';graphics.make_picture(image,lines)
            task={'task_id':f'case_{i:02d}','parameters':{},'intent':intent}
            contract={'goal':intent,'returns':{key:'str' for key in fields}}
            answer={'parameters':{},'result':fields,'source_url':'https://example.org/'}
            report=cv.assess('https://example.org/',contract,task,answer,[image],out/f'case_{i:02d}',
                             model='qwen3.5-flash',timeout_seconds=75)
            judge=report.get('judge',{})
            error=judge.get('error_type') or (report['planner_status'] if report['planner_status']!='ready' else None)
            errors=errors+1 if error else 0
            rows.append({'case':name,'expected':expected,'status':report['status'],
                'semantic_status':report.get('semantic_status'),'format_status':report.get('format_status'),
                'planner_status':report['planner_status'],'error':error,'coverage':judge.get('coverage')})
        (out/'summary.json').write_text(json.dumps({'time':datetime.now(timezone.utc).isoformat(),
          'cases':rows,'visual_sha256':visual_sha,'planner_sha256':planner_sha,
          'held_data_opened':False,'limits':'Synthetic development controls; no independent benchmark.'},indent=2)+'\n')
        print(json.dumps(rows[-1]),flush=True)
    print('OUTPUT='+str(out),flush=True)


if __name__=='__main__':main()
