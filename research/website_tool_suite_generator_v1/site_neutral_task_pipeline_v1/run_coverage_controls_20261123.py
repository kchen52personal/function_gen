"""Experimental, small two-stage verifier checks on synthetic transit pages."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4
import run_verifier_controls_20261120 as graphics
import coverage_verifier as cv
import visual_verifier as v

def main():
    out=graphics.ROOT/('verifier_coverage_controls_20261123_'+uuid4().hex[:8]);out.mkdir(mode=0o700)
    cases=[('correct','pass',['Transit schedule','Station: Cedar','Departure: 09:20','Platform: 4'],
            {'station':'Cedar','departure':'09:20','platform':'4'}),
           ('wrong_field','fail',['Transit schedule','Station: Cedar','Departure: 09:20','Platform: 4'],
            {'station':'Cedar','departure':'09:20','platform':'7'}),
           ('hidden_field','inconclusive',['Transit schedule','Station: Cedar','Departure: 09:20','Platform: not shown'],
            {'station':'Cedar','departure':'09:20','platform':'4'})]
    rows=[];errors=0
    for i,(name,expected,lines,fields) in enumerate(cases):
        if errors>=2:
            rows.append({'case':name,'expected':expected,'status':'not_run_api_failures'})
        else:
            image=out/f'image_{i:02d}.png';graphics.make_picture(image,lines)
            intent='Report the station, departure time, and platform of the listed train.'
            task={'task_id':f'case_{i:02d}','parameters':{},'intent':intent}
            contract={'goal':intent,'returns':{'station':'str','departure':'str','platform':'str'}}
            answer={'parameters':{},'result':fields,'source_url':'https://example.org/'}
            report=cv.assess('https://example.org/',contract,task,answer,[image],out/f'case_{i:02d}',
                             model='qwen3.5-flash',timeout_seconds=60)
            judge=report.get('judge',{})
            error=judge.get('error_type') or (report['planner_status'] if report['planner_status']!='ready' else None)
            errors=errors+1 if error else 0
            rows.append({'case':name,'expected':expected,'status':report['status'],
                         'planner_status':report['planner_status'],'error':error,
                         'coverage':judge.get('coverage')})
        (out/'summary.json').write_text(json.dumps({'time':datetime.now(timezone.utc).isoformat(),
           'cases':rows,'visual_sha256':hashlib.sha256(Path(v.__file__).read_bytes()).hexdigest(),
           'planner_sha256':hashlib.sha256(Path(cv.__file__).read_bytes()).hexdigest(),
           'held_data_opened':False,'limits':'Synthetic development controls; planner completeness is unproven.'},indent=2)+'\n')
        print(json.dumps(rows[-1]),flush=True)
    print('OUTPUT='+str(out),flush=True)

if __name__=='__main__':main()
