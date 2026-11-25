"""Small counterbalanced same-provider visual-judge model comparison."""
from datetime import datetime,timezone
import json
from pathlib import Path
from uuid import uuid4
import visual_verifier as v

BASE=Path('/shared/hans/research/website_tool_suite_generator_v1/automated_runs')
IMAGE=BASE/'verifier_fixture_recon_20261123_ef33a3de/recon/homepage.png'

def main():
    out=BASE/('verifier_model_pair_20261123_'+uuid4().hex[:8]);out.mkdir(mode=0o700)
    site='http://127.0.0.1:18765/'
    intent='Report the displayed price of Desk Mat XL.'
    contract={'goal':intent,'returns':{'answer':'str'}}
    task={'task_id':'case','parameters':{},'intent':intent}
    cases=[('correct','pass','$49.00'),('wrong','fail','$59.00')]
    rows=[]
    for i,(name,expected,price) in enumerate(cases):
        answer={'parameters':{},'result':{'answer':price},'source_url':site}
        models=('qwen3.5-flash','qwen3.8-flash') if i==0 else ('qwen3.8-flash','qwen3.5-flash')
        for j,model in enumerate(models):
            report=v.judge(site,contract,task,answer,[IMAGE],out/f'case_{i}_{j}',model=model,timeout_seconds=75)
            rows.append({'case':name,'expected':expected,'model':model,'returned_model':report.get('returned_model'),
                         'status':report['status'],'error_type':report.get('error_type'),
                         'elapsed_seconds':report.get('elapsed_seconds')})
            (out/'summary.json').write_text(json.dumps({'time':datetime.now(timezone.utc).isoformat(),
                'cases':rows,'limits':'Two synthetic-answer controls on one fixture image; shared provider and time-varying load confound comparison.',
                'held_data_opened':False},indent=2)+'\n')
            print(json.dumps(rows[-1]),flush=True)
    print('OUTPUT='+str(out),flush=True)

if __name__=='__main__':main()
