"""Experimental deterministic evidence batching; no model calls or gate access.

Caller-provided checks and evidence associations can be incomplete or wrong.
This planner only prevents silent dropping of explicitly named evidence.
"""
import re

ID=re.compile(r'R[1-9][0-9]?\Z')
KEY=re.compile(r'[a-zA-Z0-9_-]{1,64}\Z')
ROLES={'before','action','after','observation'}


def plan_batches(evidence,checks,*,max_images=3,require_all_evidence=True):
    result={'status':'inconclusive','batches':[],'eligible_for_pipeline_gate':False,
            'limitations':['The caller can omit requirements, mislabel evidence or supply fabricated images.',
                'A complete batch plan is not proof of correctness or independent provenance.']}
    try:
        if type(max_images) is not int or not 1<=max_images<=3:raise ValueError('invalid image limit')
        if not isinstance(evidence,list) or not 1<=len(evidence)<=20:raise ValueError('invalid evidence')
        catalog={}
        for item in evidence:
            if (not isinstance(item,dict) or set(item)!={'key','role'} or
                not isinstance(item['key'],str) or not KEY.fullmatch(item['key']) or
                item['role'] not in ROLES or item['key'] in catalog):raise ValueError('invalid evidence item')
            catalog[item['key']]=item['role']
        if not isinstance(checks,list) or not 1<=len(checks)<=12:raise ValueError('invalid checks')
        seen=set();used=set()
        for check in checks:
            if (not isinstance(check,dict) or set(check)!={'id','requirement','evidence'} or
                not isinstance(check['id'],str) or not ID.fullmatch(check['id']) or check['id'] in seen or
                not isinstance(check['requirement'],str) or not 1<=len(check['requirement'])<=1000 or
                not isinstance(check['evidence'],list) or not check['evidence'] or
                any(not isinstance(key,str) or key not in catalog for key in check['evidence']) or
                len(set(check['evidence']))!=len(check['evidence'])):raise ValueError('invalid check')
            if len(check['evidence'])>max_images:raise ValueError('a check needs too many images for one batch')
            seen.add(check['id']);used.update(check['evidence'])
        unused=sorted(set(catalog)-used)
        result['unreferenced_evidence']=unused
        if require_all_evidence and unused:raise ValueError('unreferenced evidence')
        packed=[]
        for check in checks:
            target=next((b for b in packed if len(set(b['keys'])|set(check['evidence']))<=max_images),None)
            if target is None:target={'keys':[],'source_checks':[]};packed.append(target)
            for key in check['evidence']:
                if key not in target['keys']:target['keys'].append(key)
            target['source_checks'].append(check)
        batches=[]
        for batch in packed:
            labels={key:f'image_{i+1}' for i,key in enumerate(batch['keys'])}
            batches.append({'evidence_keys':batch['keys'],'image_roles':[catalog[key] for key in batch['keys']],
                'checks':[{'id':c['id'],'requirement':c['requirement'],
                           'images':[labels[key] for key in c['evidence']]} for c in batch['source_checks']]})
        result.update(status='ready',batches=batches)
    except (ValueError,TypeError,KeyError) as exc:
        result['reason']=str(exc)
    return result
