"""Fresh synthetic controls: different domains, values and screenshots from planner tuning.

These are development checks, not a sample of real tasks or an independent benchmark.
No browser, account access or held-out pipeline inputs are used.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4
import run_verifier_controls_20261120 as graphics
import coverage_verifier as cv
import visual_verifier as v


def main():
    out=graphics.ROOT/('verifier_coverage_transfer_20261123_'+uuid4().hex[:8]);out.mkdir(mode=0o700)
    # Freeze provenance once, before any model request; source files can change
    # while a long-running process still has the old module loaded.
    visual_sha=hashlib.sha256(Path(v.__file__).read_bytes()).hexdigest()
    planner_sha=hashlib.sha256(Path(cv.__file__).read_bytes()).hexdigest()
    cases=[
      ('event_correct','pass',['Community events','Event: Harbor Lecture','Date: 18 June','Room: B2'],
       'Report the event name, date, and room.',{'event':'Harbor Lecture','date':'18 June','room':'B2'}),
      ('event_wrong_date','fail',['Community events','Event: Harbor Lecture','Date: 18 June','Room: B2'],
       'Report the event name, date, and room.',{'event':'Harbor Lecture','date':'19 June','room':'B2'}),
      ('inventory_hidden','inconclusive',['Stock dashboard','Item: Lantern','Price: $24','Stock: not shown'],
       'Report the item, price, and stock count.',{'item':'Lantern','price':'$24','stock':'8'}),
      ('message_wrong_status','fail',['Message center','Subject: Schedule','Sender: Mira','Status: unread'],
       'Report the message subject, sender, and read status.',{'subject':'Schedule','sender':'Mira','status':'read'}),
    ]
    rows=[]; errors=0
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
                         'semantic_status':report.get('semantic_status'),
                         'format_status':report.get('format_status'),
                         'planner_status':report['planner_status'],'error':error,
                         'coverage':judge.get('coverage')})
        (out/'summary.json').write_text(json.dumps({'time':datetime.now(timezone.utc).isoformat(),
          'cases':rows,'visual_sha256':visual_sha,'planner_sha256':planner_sha,
          'held_data_opened':False,'limits':'Synthetic development controls; expected labels are human-authored, not an independent benchmark.'},indent=2)+'\n')
        print(json.dumps(rows[-1]),flush=True)
    print('OUTPUT='+str(out),flush=True)


if __name__=='__main__':main()
