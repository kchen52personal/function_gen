"""Replay two saved VLM replies through updated deterministic checking, no API calls.

This tests the checker on previously observed cases and is not a new model run.
"""
import json
from pathlib import Path
from uuid import uuid4
import coverage_verifier as cv

ROOT=Path('/shared/hans/research/website_tool_suite_generator_v1/automated_runs')
SOURCE=ROOT/'verifier_coverage_transfer_b_20261123_a67d9601'


def main():
    output=ROOT/('verifier_checklist_cached_replay_20261123_'+uuid4().hex[:8]);output.mkdir(mode=0o700)
    examples=[
      ('case_01','Report the doctor, appointment date, and appointment time.',
       {'doctor':'Lin','date':'7 July','time':'11:30'}),
      ('case_02','Report the order number, current status, and arrival date.',
       {'order':'R42','status':'shipped','arrival':'2 June'}),
    ]
    rows=[]
    for case,intent,fields in examples:
        task={'task_id':case,'parameters':{},'intent':intent}
        contract={'goal':intent,'returns':{key:'str' for key in fields}}
        candidate={'parameters':{},'result':fields,'source_url':'https://example.org/'}
        def saved(path):
            content=path.read_text()
            return lambda *args:{'model':'cached-response','choices':[{'message':{'content':content}}]}
        before=json.loads((SOURCE/case/'summary.json').read_text())
        after=cv.assess('https://example.org/',contract,task,candidate,[SOURCE/(case.replace('case','image')+'.png')],
            output/case,planner_requester=saved(SOURCE/case/'planner/planner_response.txt'),
            judge_requester=saved(SOURCE/case/'judge/judge_response.txt'))
        rows.append({'case':case,'previous_status':before['status'],'new_status':after['status'],
                     'coverage':after.get('judge',{}).get('coverage')})
    (output/'summary.json').write_text(json.dumps({'source':str(SOURCE),'cases':rows,'api_calls':0,
        'limitations':['Post-hoc replay of cached model outputs; no new or independent evaluation.']},indent=2)+'\n')
    print(json.dumps({'output':str(output),'cases':rows},indent=2))


if __name__=='__main__':main()
