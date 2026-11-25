"""Score archived verifier decisions against a separately supplied labeled manifest.

No model calls, browser actions or task generation. The manifest should be frozen
and hidden from the verifier under test; small synthetic sets are not deployment
accuracy estimates. API failures are excluded from semantic decision counts.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

LABELS={'pass','fail','inconclusive'}


def score(cases):
    if not isinstance(cases,list) or not cases:
        raise ValueError('cases must be a nonempty list')
    ids=set(); counts=Counter(); matrix=Counter(); groups={}; details=[]
    for row in cases:
        if not isinstance(row,dict): raise ValueError('invalid case')
        case_id=row.get('case_id'); expected=row.get('expected'); category=row.get('category')
        actual=row.get('actual'); execution=row.get('execution','completed')
        if not isinstance(case_id,str) or not case_id or case_id in ids: raise ValueError('missing or duplicate case_id')
        if expected not in LABELS or not isinstance(category,str) or not category: raise ValueError('invalid label or category')
        if execution not in {'completed','api_error','not_run'}: raise ValueError('invalid execution')
        if execution=='completed' and actual not in LABELS: raise ValueError('invalid decision')
        if execution!='completed' and actual is not None: raise ValueError('unexecuted case cannot have decision')
        ids.add(case_id); group=groups.setdefault(category,Counter()); group['total']+=1; counts['total']+=1
        outcome=execution
        if execution=='completed':
            counts['judged']+=1;group['judged']+=1;matrix[(expected,actual)]+=1
            if expected==actual: outcome='expected_match'
            elif actual=='inconclusive': outcome='abstention'
            elif actual=='pass': outcome='false_pass'
            else: outcome='false_fail'
        counts[outcome]+=1;group[outcome]+=1
        details.append({'case_id':case_id,'category':category,'expected':expected,'actual':actual,
                        'execution':execution,'outcome':outcome})
    return {'counts':dict(counts),'confusion':{expected:{actual:matrix[(expected,actual)]
               for actual in sorted(LABELS)} for expected in sorted(LABELS)},
            'categories':{key:dict(value) for key,value in sorted(groups.items())},'cases':details,
            'limitations':['Labels may be wrong or subjective.',
                'Synthetic or development cases do not estimate real-world generalization.',
                'A comparison requires the same frozen cases, evidence and labeling policy.',
                'False-pass and false-fail include definitive answers where expected is inconclusive.']}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('manifest',type=Path);ap.add_argument('output',type=Path)
    args=ap.parse_args();raw=args.manifest.read_bytes(); manifest=json.loads(raw)
    result=score(manifest['cases']);result.update(manifest_sha256=hashlib.sha256(raw).hexdigest(),
        generated_at=datetime.now(timezone.utc).isoformat(),label_source=manifest.get('label_source','unspecified'),
        independence=manifest.get('independence','unspecified'))
    args.output.mkdir(parents=True,exist_ok=False,mode=0o700)
    (args.output/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'counts':result['counts'],'output':str(args.output)},ensure_ascii=False))


if __name__=='__main__':main()
