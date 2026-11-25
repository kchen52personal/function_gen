"""Bounded offline-fixture plus saved-development-artifact VLM trial.

No WebWright rerun and no held-out task manifests are read.
"""
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import visual_verifier as verifier

ROOT=Path('/shared/hans/research/website_tool_suite_generator_v1')
OUT=ROOT/'automated_runs/visual_verifier_trials_20261117_v1'


def create_image(path, partial):
    image=Image.new('RGB',(1100,650),'white')
    draw=ImageDraw.Draw(image)
    try: font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',31)
    except OSError: font=ImageFont.load_default()
    lines=['Example catalog - items and prices','Birch lamp             $12.00',
           'Cedar chair            $16.00']
    if not partial: lines.append('Maple table            $24.00')
    for index,line in enumerate(lines): draw.text((45,45+100*index),line,font=font,fill='black')
    image.save(path)


def control_cases(root):
    screen=root/'complete.png'; partial=root/'partial.png'
    create_image(screen,False); create_image(partial,True)
    contract={'goal':'Return all three catalog items with name and price, sorted by price ascending.',
              'returns':{'items':'list[object]'}}
    task={'task_id':'control','intent':'Return all three catalog items with name and price, sorted by price ascending.',
          'parameters':{'catalog':'demo'}}
    items=[{'name':'Birch lamp','price':12.0},{'name':'Cedar chair','price':16.0},
           {'name':'Maple table','price':24.0}]
    def answer(rows): return {'parameters':task['parameters'],'result':{'items':rows},'source_url':'https://example.org/'}
    return [
      ('correct','pass',screen,contract,task,answer(items)),
      ('wrong_price','fail',screen,contract,task,answer([items[0],{'name':'Cedar chair','price':19.0},items[2]])),
      ('wrong_order','fail',screen,contract,task,answer(list(reversed(items)))),
      ('missing_evidence','inconclusive',partial,contract,task,answer(items)),
    ]


def saved_case(name,run,task_id,final_run):
    root=ROOT/'automated_runs'/run
    summary=json.loads((root/'summary.json').read_text())
    tasks=json.loads((root/'tasks/development/tasks.json').read_text())['tasks']
    task=next(t for t in tasks if t['task_id']==task_id)
    saved=root/'development_rounds/round_00'/task_id/'qwen3.5-plus/agent/final_runs'/final_run
    result,legacy=verifier.parse_result_log((saved/'final_script_log.txt').read_text())
    return name,summary['website'],summary['contract'],task,result,[saved/'screenshots/before.png',saved/'screenshots/after.png'],legacy


def main():
    OUT.mkdir(parents=True,exist_ok=False)
    controls=OUT/'control_images';controls.mkdir()
    records=[]; failures=0
    for name,gold,screen,contract,task,result in control_cases(controls):
        if failures>=2:
            records.append({'case':name,'expected_for_fixture':gold,'status':'skipped_after_api_failures'});continue
        report=verifier.judge('https://example.org/',contract,task,result,[screen],OUT/name)
        failures=failures+1 if report.get('error_type') else 0
        records.append({'case':name,'expected_for_fixture':gold,'status':report['status'],
                        'error_type':report.get('error_type'),'format':report['format']['status']})
        print(name,report['status'],flush=True)
    saved=[('books','books_8dev_webwright_only_20261116_v2','task_003','run_002'),
           ('arxiv','arxiv_8dev_webwright_only_20261116_v1','task_003','run_001')]
    for args in saved:
        name,site,contract,task,result,images,legacy=saved_case(*args)
        if failures>=2:
            records.append({'case':name,'status':'skipped_after_api_failures'});continue
        report=verifier.judge(site,contract,task,result,images,OUT/name,legacy_marker=legacy)
        failures=failures+1 if report.get('error_type') else 0
        records.append({'case':name,'status':report['status'],'error_type':report.get('error_type'),
                        'format':report['format']['status'],'gold':'not established'})
        print(name,report['status'],'format',report['format']['status'],flush=True)
    (OUT/'experiment_summary.json').write_text(json.dumps({'cases':records,'held_opened':False,
        'interpretation':'Small controlled smoke test, not a general verifier accuracy estimate.'},indent=2)+'\n')

if __name__=='__main__': main()
