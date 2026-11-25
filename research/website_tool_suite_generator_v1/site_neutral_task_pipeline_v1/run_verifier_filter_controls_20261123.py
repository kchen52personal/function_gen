"""Synthetic list-filter controls; separate from site data and held-out tasks."""
from datetime import datetime, timezone
import json
from uuid import uuid4
import run_verifier_controls_20261120 as graphics
import visual_verifier as verifier

def main():
    out=graphics.ROOT/('verifier_filter_controls_20261123_'+uuid4().hex[:8]);out.mkdir(mode=0o700)
    full=out/'full.png';partial=out/'partial.png'
    graphics.make_picture(full,['Inbox - all messages','Unread | Mira | Planning',
        'Read | Mira | Budget','Unread | Mira | Schedule','Unread | Leo | Update','End of message list'])
    graphics.make_picture(partial,['Inbox - some messages','Unread | Mira | Planning',
        'Read | Mira | Budget','Additional messages below, not visible'])
    contract={'goal':'List the subjects of all unread messages from Mira.','returns':{'subjects':'list[str]'}}
    task={'task_id':'synthetic','parameters':{},'intent':'List all unread messages from Mira by subject.'}
    def answer(subjects):
        return {'parameters':{},'result':{'subjects':subjects},'source_url':'https://example.org/'}
    cases=[('correct','pass',full,answer(['Planning','Schedule'])),
           ('read_message_included','fail',full,answer(['Planning','Budget','Schedule'])),
           ('partial_view','inconclusive',partial,answer(['Planning','Schedule']))]
    rows=[];failures=0
    for i,(name,expected,image,result) in enumerate(cases):
        if failures>=2:
            rows.append({'case':name,'expected':expected,'status':'not_run_api_failures'})
        else:
            report=verifier.judge('https://example.org/',contract,task,result,[image],out/f'case_{i:02d}',
                                  model='qwen3.5-flash',timeout_seconds=75)
            failures=failures+1 if report.get('error_type') else 0
            rows.append({'case':name,'expected':expected,'status':report['status'],
                         'format':report['format']['status'],'error_type':report.get('error_type')})
        (out/'summary.json').write_text(json.dumps({'time':datetime.now(timezone.utc).isoformat(),
            'cases':rows,'limits':'Hand-authored exploratory examples, not a blind reliability benchmark.',
            'held_data_opened':False},indent=2)+'\n')
        print(json.dumps(rows[-1]),flush=True)
    print('OUTPUT='+str(out),flush=True)

if __name__=='__main__': main()
