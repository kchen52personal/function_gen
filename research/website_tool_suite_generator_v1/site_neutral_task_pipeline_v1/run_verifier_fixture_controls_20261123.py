"""Known visual checks using a real screenshot of the offline fixture homepage."""
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4
import visual_verifier as verifier

BASE=Path('/shared/hans/research/website_tool_suite_generator_v1/automated_runs')
IMAGE=BASE/'verifier_fixture_recon_20261123_ef33a3de/recon/homepage.png'

def main():
    out=BASE/('verifier_fixture_controls_20261123_'+uuid4().hex[:8]);out.mkdir(mode=0o700)
    cases=[('featured_price_correct','pass','Report the displayed price of Desk Mat XL.','$49.00'),
           ('featured_price_wrong','fail','Report the displayed price of Desk Mat XL.','$59.00'),
           ('shipping_correct','pass','Report the order threshold for free shipping.','$75.00')]
    rows=[]; errors=0
    for i,(name,expected,intent,value) in enumerate(cases):
        if errors>=2:
            rows.append({'case':name,'expected':expected,'status':'not_run_api_failures'})
        else:
            contract={'goal':intent,'returns':{'answer':'str'}}
            task={'task_id':f'case_{i:02d}','parameters':{},'intent':intent}
            site='http://127.0.0.1:18765/'
            answer={'parameters':{},'result':{'answer':value},'source_url':site}
            report=verifier.judge(site,contract,task,answer,[IMAGE],out/f'case_{i:02d}',
                                  model='qwen3.5-flash',timeout_seconds=90)
            errors=errors+1 if report.get('error_type') else 0
            rows.append({'case':name,'expected':expected,'status':report['status'],
                         'error_type':report.get('error_type'),'elapsed_seconds':report.get('elapsed_seconds')})
        (out/'summary.json').write_text(json.dumps({'time':datetime.now(timezone.utc).isoformat(),
          'cases':rows,'image_source':str(IMAGE),'held_data_opened':False,
          'limits':'Labels derived from visible local fixture screenshot; no real-site generalization claim.'},indent=2)+'\n')
        print(json.dumps(rows[-1]),flush=True)
    print('OUTPUT='+str(out),flush=True)

if __name__=='__main__':main()
