"""Experimental multi-batch visual diagnostics; not a pipeline acceptance gate.

The caller supplies every requirement and exact evidence mapping. This cannot
detect omitted requirements, authenticate images, or certify model judgments.
"""
import json
from pathlib import Path
from evidence_batch_planner import plan_batches
import visual_verifier as visual


def assess(site,contract,task,result,evidence,checks,output,*,requester=None,allow_external=False,
           model='qwen3.5-flash',timeout_seconds=90):
    output=Path(output);output.mkdir(mode=0o700,parents=True,exist_ok=False)
    report={'status':'inconclusive','diagnostic_status':None,'eligible_for_pipeline_gate':False,
            'is_ground_truth':False,'batches':[],
            'limitations':['The caller can omit checks or mislabel and fabricate evidence.',
                'Model citations may be incorrect; batch agreement is not proof of success.']}
    def save():
        (output/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        return report
    try:
        if not isinstance(evidence,list) or any(not isinstance(x,dict) or
            not {'key','role','path'}<=set(x) or set(x)-{'key','role','path','metadata'} or
            ('metadata' in x and not isinstance(x['metadata'],dict)) for x in evidence):
            report['reason']='invalid evidence';return save()
        plan=plan_batches([{'key':x['key'],'role':x['role']} for x in evidence],checks)
        report['plan']=plan
        if plan['status']!='ready':report['reason']='evidence plan unavailable';return save()
        if requester is None and not allow_external:
            report['reason']='external model use is not authorized by the caller';return save()
        if not isinstance(task,dict) or 'observer_evidence' in task:
            report['reason']='invalid or reserved task metadata';return save()
        paths={x['key']:Path(x['path']) for x in evidence}
        metadata={x['key']:x.get('metadata',{}) for x in evidence}
        for index,batch in enumerate(plan['batches']):
            batch_task={**task,'observer_evidence':{'caveat':'Caller-provided, potentially incomplete or misleading; not proof.',
                'images':[{'image_id':f'image_{i+1}','metadata':metadata[k]}
                          for i,k in enumerate(batch['evidence_keys'])]}}
            assessment=visual.judge(site,contract,batch_task,result,[paths[k] for k in batch['evidence_keys']],
                output/f'batch_{index:02d}',requester=requester,model=model,timeout_seconds=timeout_seconds,
                image_roles=batch['image_roles'],required_checks=batch['checks'])
            report['batches'].append({'index':index,'status':assessment['status'],
                'format_status':assessment['format']['status'],'error_type':assessment.get('error_type'),
                'checks':[x['id'] for x in batch['checks']], 'coverage':assessment.get('coverage')})
            if assessment.get('error_type'):
                report['reason']='batch model call failed; remaining batches not run';return save()
        if any(b['status']=='inconclusive' for b in report['batches']):
            report['reason']='one or more batch judgments were inconclusive';return save()
        report['diagnostic_status']=('fail' if any(b['status']=='fail' or b['format_status']=='fail'
                                                   for b in report['batches']) else 'pass')
        report['status']='diagnostic_complete'
    except (OSError,ValueError,TypeError,KeyError) as exc:
        report.update(reason='batch assessment unavailable: '+type(exc).__name__,error_type=type(exc).__name__)
    return save()
