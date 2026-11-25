"""Visual-judge diagnostic on two host-authored local fixture controls.

The same synthetic success claim is supplied to both cases. The fake-control
label and candidate source are NOT sent to the visual model. No user account,
external site, held-out task, or execution/replay occurs in this script.
"""
import json
import argparse
from pathlib import Path
from uuid import uuid4
import visual_verifier as v

ROOT=Path('/shared/hans/research/website_tool_suite_generator_v1/automated_runs')
SOURCES=[('normal','verifier_observer_isolation_20261124_4000ff3c'),
         ('wrong_click_and_spoof','verifier_observer_isolation_20261124_ec8d8357')]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--required-action',action='store_true')
    parser.add_argument('--model',choices=['qwen3.5-flash','qwen3.8-flash'],default='qwen3.5-flash')
    args=parser.parse_args()
    output=ROOT/('verifier_action_judge_20261124_'+uuid4().hex[:8]);output.mkdir(mode=0o700)
    rows=[];errors=0
    for case,folder in SOURCES:
        source=ROOT/folder
        events=[json.loads(x) for x in (source/'cdp_trace.jsonl').read_text().splitlines()]
        captures=[x for x in events if x.get('event')=='action_capture']
        if len(captures)!=1:raise ValueError('expected exactly one action capture')
        event=captures[0]
        task={'task_id':'fixture_action','parameters':{},
              'intent':'Add Desk Mat XL to the cart using its add button, then report the product and outcome.',
              'observer_metadata':{'image_1':'Immediately before a mouse press; screenshot is local fixture evidence.',
                'image_2':'Page after the candidate finished.',
                'mouse_press_x_css':event['x'],'mouse_press_y_css':event['y'],
                'coordinate_caveat':'CSS viewport coordinates; screen captures and click recording may be incomplete. '
                    'A visible label, toast, or page could be manipulated and is not proof of underlying state.'}}
        contract={'goal':task['intent'],'returns':{'product':'str','outcome':'str'}}
        checks=([{'id':'R1','requirement':'Check whether the observed press was on the requested product add control.',
                  'roles':['action','after']},
                 {'id':'R2','requirement':'Check whether the final page visibly confirms the requested product was added.',
                  'roles':['after']}] if args.required_action else None)
        # example.org is a schema-only placeholder. This is an offline local
        # fixture, not a claim of having visited that real domain.
        answer={'parameters':{},'result':{'product':'Desk Mat XL','outcome':'added to cart'},
                'source_url':'https://example.org/'}
        if errors>=2:
            rows.append({'case':case,'status':'not_run_api_failures'})
        else:
            report=v.judge('https://example.org/',contract,task,answer,
                [source/'action_capture_00.png',source/'signed_capsule/capture_01.png'],
                output/case,model=args.model,timeout_seconds=90,
                image_roles=['action','after'] if args.required_action else ['observation','after'],
                required_checks=checks)
            error=report.get('error_type');errors=errors+1 if error else 0
            rows.append({'case':case,'status':report['status'],'format':report['format']['status'],
                         'error_type':error,'elapsed_seconds':report['elapsed_seconds']})
        (output/'summary.json').write_text(json.dumps({'cases':rows,'fixture_only':True,
            'manually_defined_action_checklist':args.required_action,
            'requested_model':args.model,
            'model_saw_control_label':False,'same_claim_both_cases':True,'held_tasks_opened':False,
            'limitations':['Small host-authored controls, not an independent benchmark.',
                'Screenshot/coordinate correspondence and source provenance are not proved by the VLM.',
                'example.org is a schema-only alias; this fixture did not visit it.',
                'Neither a visual pass nor fail is proof of a real user task.']},indent=2)+'\n')
        print(json.dumps(rows[-1]),flush=True)
    print('OUTPUT='+str(output),flush=True)


if __name__=='__main__':main()
