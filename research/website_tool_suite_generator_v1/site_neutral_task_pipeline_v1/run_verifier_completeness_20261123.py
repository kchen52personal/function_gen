"""Synthetic controls for visible-only versus all-results completeness."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4
import run_verifier_controls_20261120 as graphics
import visual_verifier as v


def main():
    out=graphics.ROOT/('verifier_completeness_20261123_'+uuid4().hex[:8]);out.mkdir(mode=0o700)
    source_sha=hashlib.sha256(Path(v.__file__).read_bytes()).hexdigest()
    cases=[
       ('all_three','pass',['Library','Total results: 3','Elm','Oak','Pine'],
        'Return all three listed titles.',['Elm','Oak','Pine']),
       ('omitted_one','fail',['Library','Total results: 3','Elm','Oak','Pine'],
        'Return all three listed titles.',['Elm','Oak']),
       ('visible_only','pass',['Library','Total results: 8','Visible: Elm, Oak, Pine','Other results offscreen'],
        'Return the titles currently visible on screen.',['Elm','Oak','Pine']),
       ('unseen_answer','inconclusive',['Library','Total results: 8','Visible: Elm, Oak, Pine','Other results offscreen'],
        'Return all eight result titles.',['Elm','Oak','Pine','Birch','Ash','Fir','Beech','Cedar']),
    ]
    rows=[];errors=0
    for i,(name,expected,lines,intent,titles) in enumerate(cases):
        if errors>=2: rows.append({'case':name,'expected':expected,'status':'not_run_api_failures'})
        else:
            image=out/f'image_{i:02d}.png';graphics.make_picture(image,lines)
            task={'task_id':f'case_{i:02d}','parameters':{},'intent':intent}
            contract={'goal':intent,'returns':{'titles':'list[str]'}}
            answer={'parameters':{},'result':{'titles':titles},'source_url':'https://example.org/'}
            report=v.judge('https://example.org/',contract,task,answer,[image],out/f'case_{i:02d}',
                           model='qwen3.5-flash',timeout_seconds=75)
            error=report.get('error_type');errors=errors+1 if error else 0
            rows.append({'case':name,'expected':expected,'status':report['status'],
                         'format':report['format']['status'],'error':error,'elapsed_seconds':report['elapsed_seconds']})
        (out/'summary.json').write_text(json.dumps({'time':datetime.now(timezone.utc).isoformat(),
           'cases':rows,'visual_sha256':source_sha,'held_data_opened':False,
           'limitations':['Hand-authored synthetic tests, not an independent benchmark.']},indent=2)+'\n')
        print(json.dumps(rows[-1]),flush=True)
    print('OUTPUT='+str(out),flush=True)


if __name__=='__main__':main()
