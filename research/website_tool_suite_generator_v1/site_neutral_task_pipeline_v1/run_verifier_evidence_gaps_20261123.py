"""Evidence-sufficiency stress tests; absence of proof is not proof of failure."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4
import run_verifier_controls_20261120 as graphics
import visual_verifier as v

def main():
    out=graphics.ROOT/('verifier_evidence_gaps_20261123_'+uuid4().hex[:8]);out.mkdir(mode=0o700)
    cases=[
      ('unseen_time','Report whether the report was updated today.', ['Report','Last updated: date hidden'], 'updated today',None),
      ('unseen_pages','Report the number of matches across all pages.', ['Search','No items on this page','More pages exist; other pages not shown'], '0',None),
      ('no_action','Confirm that no item was ever deleted.', ['Files','File A','File B'], 'no deletion occurred',['Files','File A','File B']),
      ('link_destination','Report the destination URL of the document link.', ['Results','Document: Meeting notes','URL destination not visible'], 'https://example.org/doc',None),
      ('audio','Confirm that audio was audible to the listener.', ['Player','Song: Morning Light','Play icon is shown','Speaker status unavailable'], 'audio was audible',None),
      ('first_result','Confirm the opened article was the first search result.', ['Article','Title: Ocean currents','Search ranking not shown'], 'first search result',None),
    ]
    rows=[];errors=0;digest=hashlib.sha256(Path(v.__file__).read_bytes()).hexdigest()
    for i,(name,intent,lines,value,before_lines) in enumerate(cases):
        if errors>=3:
            rows.append({'case':name,'status':'not_run_api_failures','expected':'inconclusive'})
            (out/'summary.json').write_text(json.dumps({'time':datetime.now(timezone.utc).isoformat(),
                'verifier_sha256':digest,'cases':rows,'held_data_opened':False,
                'limits':'Synthetic adversarial controls; expected labels are hand-authored.'},indent=2)+'\n')
            print(json.dumps(rows[-1]),flush=True)
            continue
        image=out/f'after_{i:02d}.png';graphics.make_picture(image,lines)
        images=[];roles=[]
        if before_lines:
            before=out/f'before_{i:02d}.png';graphics.make_picture(before,before_lines)
            images.append(before);roles.append('before')
        images.append(image);roles.append('after' if before_lines else 'observation')
        task={'task_id':f'case_{i:02d}','parameters':{},'intent':intent}
        contract={'goal':intent,'returns':{'answer':'str'}}
        result={'parameters':{},'result':{'answer':value},'source_url':'https://example.org/'}
        report=v.judge('https://example.org/',contract,task,result,images,out/f'case_{i:02d}',
                       model='qwen3.5-flash',timeout_seconds=75,image_roles=roles)
        errors=errors+1 if report.get('error_type') else 0
        rows.append({'case':name,'expected':'inconclusive','status':report['status'],
                     'error_type':report.get('error_type'),'elapsed_seconds':report.get('elapsed_seconds')})
        (out/'summary.json').write_text(json.dumps({'time':datetime.now(timezone.utc).isoformat(),
           'verifier_sha256':digest,'cases':rows,'held_data_opened':False,
           'limits':'Synthetic adversarial controls; inconclusive is the hand-authored expected label.'},indent=2)+'\n')
        print(json.dumps(rows[-1]),flush=True)
    print('OUTPUT='+str(out),flush=True)

if __name__=='__main__':main()
