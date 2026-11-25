"""Controlled prefix-of-trajectories benchmark on an isolated, fixed catalog.

The host grades from source catalog data, never shares gold with models, and
freezes all candidates before opening the held manifest in the experiment.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import socket
import sys
import time

HERE=Path(__file__).resolve().parent
PROJECT=HERE.parent
sys.path[:0]=[str(HERE/'runtime'),str(PROJECT/'pipeline_codex_v73')]
from codex_bounded_process import run_bounded
from runtime.fixture_session import FixtureSession, FIXTURE
from synthesis import synthesize, trajectory_excerpt

BENCH=PROJECT/'automated_runs/checkpoint_benchmark_20261118_v1'
SIZES=(2,3,4,5,6,8)
CATEGORIES=('Gear','Apparel','Home','Accessories')
URL='http://127.0.0.1:18765/'
CONTRACT={
 'goal':'Open a catalog category, sort by price in the requested direction, and return every product in that category with price <= max_price, in displayed price order. All eligible products must be included, using Load more if necessary. Filtering the extracted page data in Python is allowed.',
 'arguments':[{'name':'category_id','type':'integer','description':'Category index: 0=Gear, 1=Apparel, 2=Home, 3=Accessories.'},
              {'name':'max_price','type':'number','description':'Inclusive maximum price in USD; nonnegative.'},
              {'name':'descending','type':'boolean','description':'True sorts price high to low; false sorts low to high.'}],
 'returns':{'items':'list[object]'},
 'success_contract':'Return exactly result.items as an ordered list of objects with name (product title string) and price (USD number). No other item fields. Exclude products above max_price. Return [] if none. Price ties use name ascending. category_id must be 0..3. The CLI boolean must accept --descending true or --descending false.'}


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path,value):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,indent=2)+'\n')
    os.replace(temp,path)


def catalog():
    asset=FIXTURE/'dist/assets/index-7Zc1JihY.js'
    pattern=r'title:"([^"\\]+)",price:(\d+(?:\.\d+)?),sku:"[^"\\]+",cat:"([^"\\]+)"'
    rows=[{'name':n,'price':float(p),'category':c} for n,p,c in re.findall(pattern,asset.read_text())]
    if len(rows)!=16 or len({x['name'] for x in rows})!=16: raise ValueError('catalog extraction mismatch')
    return rows


def expected(params,rows):
    cat=('gear','apparel','home','accessories')[params['category_id']]
    values=[{'name':x['name'],'price':x['price']} for x in rows
            if x['category']==cat and x['price']<=params['max_price']]
    return sorted(values,key=lambda x:((-x['price'] if params['descending'] else x['price']),x['name']))


def record(i,c,p,d):
    params={'category_id':c,'max_price':p,'descending':d}
    order='high to low' if d else 'low to high'
    return {'task_id':f'task_{i:03d}','parameters':params,
            'intent':f'Open {CATEGORIES[c]}, sort price {order}, and report every product costing at most ${p} with its name and price.'}


def prepare():
    BENCH.mkdir(parents=True,exist_ok=False)
    dev=[record(i+1,c,p,d) for i,(c,p,d) in enumerate(
        [(0,75,False),(1,125,True),(2,175,False),(3,225,True),
         (0,125,True),(1,175,False),(2,225,True),(3,75,False),
         (0,85,False),(1,135,True),(2,195,False),(3,65,True),
         (0,135,True),(1,195,False),(2,65,True),(3,85,False)])]
    held=[record(101+i,c,p,d) for i,(c,p,d) in enumerate(
        [(c,p,d) for c in range(4) for d in (False,True) for p in (0,30,50,100,200)])]
    random.Random(20261118).shuffle(held)
    signatures=lambda tasks:{json.dumps(x['parameters'],sort_keys=True) for x in tasks}
    if signatures(dev)&signatures(held) or len(signatures(held))!=40: raise ValueError('split collision')
    save(BENCH/'development.json',{'tasks':dev})
    save(BENCH/'held.json',{'tasks':held})
    save(BENCH/'contract.json',CONTRACT)
    rows=catalog()
    outputs=[json.dumps(expected(t['parameters'],rows),sort_keys=True) for t in held]
    plan={'created_at':datetime.now(timezone.utc).isoformat(),'sizes':SIZES,'held_count':40,
          'development_pool_count':16,'maximum_development_attempts':16,'selection':'first eight submitted AND independently exact-correct scripts, in fixed attempt order',
          'development_sha256':digest(BENCH/'development.json'),'held_sha256':digest(BENCH/'held.json'),
          'contract_sha256':digest(BENCH/'contract.json'),
          'fixture_asset_sha256':digest(FIXTURE/'dist/assets/index-7Zc1JihY.js'),
          'distinct_held_expected_outputs':len(set(outputs)),'held_empty_result_count':sum(x=='[]' for x in outputs),
          'grading':'exact ordered names and numeric USD prices; failed, missing, or malformed outputs count as incorrect; format and execution rates separate',
          'overall_seconds':21600,'agent_calls_per_attempt':30,'inner_calls_per_attempt':6,
          'per_development_seconds':1200,'synthesis_calls_per_checkpoint':1,
          'limitations':['Fixed, host-authored benchmark manifests; this does not evaluate autonomous task generation.',
            'One task family on a small fixed 16-product demo; near-duplicate outputs and in-distribution specialization are expected.',
            'Selection is conditioned on successful development tasks; report every failed attempt.',
            'One synthesis per size; model randomness and trajectory composition are confounded with count.',
            'Held scores are exploratory and cannot subsequently guide tuning while retaining an untouched-test claim.',
            'No human or interactive assistant writes candidate programs during the benchmark.']}
    save(BENCH/'plan.json',plan)
    save(BENCH/'summary.json',{'status':'prepared','held_opened':False,'attempts':[],'checkpoints':[]})
    return plan


def parse_result(log):
    lines=[line[len('final result: '):] for line in log.splitlines() if line.startswith('final result: ')]
    if len(lines)!=1: raise ValueError('expected exactly one final result marker')
    return json.loads(lines[0])


def valid_output(value,params):
    if not isinstance(value,dict) or set(value)!={'parameters','result','source_url'}: return False
    if value['parameters']!=params or value['source_url']!=URL: return False
    echoed=value['parameters']
    if (type(echoed.get('category_id')) is not int or type(echoed.get('descending')) is not bool
        or type(echoed.get('max_price')) not in (int,float)): return False
    if not isinstance(value['result'],dict) or set(value['result'])!={'items'}: return False
    items=value['result']['items']
    if not isinstance(items,list): return False
    for item in items:
        if (not isinstance(item,dict) or set(item)!={'name','price'} or not isinstance(item['name'],str)
            or type(item['price']) not in (int,float) or not math.isfinite(item['price'])
            or item['price']<0): return False
    return True


def grade(value,params,rows):
    return valid_output(value,params) and value['result']['items']==expected(params,rows)


def fresh_evaluate(session,script,task,out,rows,*,generalized):
    out.mkdir(parents=True,exist_ok=False)
    copy=out/'candidate.py'
    shutil.copyfile(script,copy)
    params=task['parameters']
    args=['python3',str(copy)]
    if generalized:
        args += [part for k,v in params.items() for part in (f'--{k}',str(v).lower() if type(v) is bool else str(v))]
        args += ['--output-dir',str(out/'execution')]
    result=session.run(out,args)
    (out/'stdout.txt').write_text(result.stdout)
    (out/'stderr.txt').write_text(result.stderr)
    state={'task_id':task['task_id'],'process_exit':result.returncode,'execution_ok':result.returncode==0,
           'format_ok':False,'exact_correct':False}
    logs=list(out.rglob('final_script_log.txt'))
    try:
        if len(logs)!=1: raise ValueError('expected one result log')
        value=parse_result(logs[0].read_text())
        state['format_ok']=valid_output(value,params)
        state['exact_correct']=result.returncode==0 and grade(value,params,rows)
    except (ValueError,OSError,TypeError) as exc:
        state['parse_error']=str(exc)[:200]
    save(out/'summary.json',state)
    return state


def task_spec(task):
    params=task['parameters']
    return {'task':task['intent']+' Save evidence and submit the reusable browser script. '
        'Only read from the browser; do not change cart, check out, use source files or make direct HTTP requests. '
        'The script must derive names and numeric prices from the page, include all eligible products, and write exactly '
        'one final line in final_script_log.txt beginning "final result: " followed by JSON with '
        'parameters equal to '+json.dumps(params)+', result equal to {"items":[{"name":"product title","price":0}]}, '
        'and source_url equal to "'+URL+'". The item object is a structural example, not an answer. '
        'Price filtering after extracting browser data is allowed. Sort ties by name ascending. '
        'Save at most two final screenshots to keep self-check within budget.',
        'success_criteria':[task['intent'],'Report ordered names and numeric prices read from the page in the required JSON log.',
                            'Complete the built-in self-check and submit.'],
        'requested_constraints':['Read-only local catalog task. Do not change cart or check out.']}


def run():
    plan=json.loads((BENCH/'plan.json').read_text())
    state=json.loads((BENCH/'summary.json').read_text())
    if state['status']!='prepared': raise ValueError('refusing to rerun or overwrite an experiment')
    if (digest(BENCH/'development.json')!=plan['development_sha256']
        or digest(BENCH/'held.json')!=plan['held_sha256']
        or digest(BENCH/'contract.json')!=plan['contract_sha256']
        or digest(FIXTURE/'dist/assets/index-7Zc1JihY.js')!=plan['fixture_asset_sha256']):
        raise ValueError('frozen experiment inputs changed')
    dev=json.loads((BENCH/'development.json').read_text())['tasks']
    rows=catalog()
    selected=[]
    started=time.monotonic()
    state.update(status='development_running',started_at=datetime.now(timezone.utc).isoformat(),
                 process_identity={'pid':os.getpid(),'hostname':socket.gethostname()})
    def checkpoint(): save(BENCH/'summary.json',state)
    checkpoint()
    try:
        with FixtureSession() as session:
            for i,task in enumerate(dev,1):
                if time.monotonic()-started>plan['overall_seconds']-1800: break
                spec=BENCH/f'dev_spec_{i:02d}.json';save(spec,task_spec(task))
                name=f'checkpoint_dev_20261118_{i:02d}'
                workspace=PROJECT/f'ct_checkpoint_20261118_{i:02d}'
                cmd=[sys.executable,str(HERE/'runtime/container_fixture_webwright_smoke.py'),
                     '--run-name',name,'--workspace-name',workspace.name,'--agent-calls','30',
                     '--inner-calls','6','--wall-seconds','1200','--task-file',str(spec)]
                code,stdout,stderr=run_bounded(cmd,cwd=PROJECT,env=os.environ.copy(),timeout=1280)
                (BENCH/f'dev_{i:02d}.stdout.txt').write_text(stdout)
                (BENCH/f'dev_{i:02d}.stderr.txt').write_text(stderr)
                summary_path=PROJECT/'automated_runs'/name/'summary.json'
                info=json.loads(summary_path.read_text()) if summary_path.exists() else {}
                item={'task_id':task['task_id'],'run':name,'process_exit':code,
                      'trajectory_status':info.get('trajectory_status'),'accepted':False}
                script=workspace/'final_script.py'
                if info.get('trajectory_status')=='Submitted' and info.get('process_exit')==0 and script.is_file() and not script.is_symlink():
                    assessment=fresh_evaluate(session,script,task,BENCH/'development_checks'/task['task_id'],rows,generalized=False)
                    item['assessment']=assessment
                    if assessment['exact_correct']:
                        frozen=BENCH/'accepted'/f'{len(selected)+1:02d}.py'
                        frozen.parent.mkdir(exist_ok=True)
                        shutil.copyfile(script,frozen)
                        selected.append({'task':task,'script':str(frozen),'sha256':digest(frozen),'workspace':str(workspace)})
                        item['accepted']=True
                state['attempts'].append(item)
                state['accepted_count']=len(selected)
                save(BENCH/'accepted_manifest.json',selected)
                checkpoint()
                if len(selected)==8: break
            if len(selected)<2:
                state.update(status='stopped_fewer_than_two_accepted_trajectories',held_opened=False)
                checkpoint();return
            for n in SIZES:
                if n>len(selected): continue
                if time.monotonic()-started>plan['overall_seconds']-1800: break
                root=BENCH/'checkpoints'/f'{n:02d}'
                examples=[]
                for item in selected[:n]:
                    source=Path(item['script'])
                    if digest(source)!=item['sha256']: raise ValueError('accepted script changed')
                    examples.append({'task':item['task'],'script':source.read_text(),
                                     'assessment':{'status':'exact_correct_on_development_case'},
                                     'trajectory':trajectory_excerpt(Path(item['workspace']))})
                request=BENCH/f'synthesis_request_{n:02d}.json'
                save(request,{'examples':examples,'output':str(root/'synthesis')})
                code,out,err=run_bounded([sys.executable,str(__file__),'synthesize','--request',str(request)],
                    cwd=PROJECT,env=os.environ.copy(),timeout=340)
                root.mkdir(parents=True,exist_ok=True)
                (root/'worker.stdout.txt').write_text(out);(root/'worker.stderr.txt').write_text(err)
                summary=root/'synthesis/summary.json'
                info=json.loads(summary.read_text()) if summary.exists() else {}
                entry={'n':n,'status':info.get('status','synthesis_failed'),'held_results':[]}
                if code==0 and info.get('status')=='candidate_ready':
                    frozen=root/'frozen.py';shutil.copyfile(root/'synthesis/candidate.py',frozen)
                    entry.update(status='frozen',sha256=digest(frozen),source_task_ids=[x['task']['task_id'] for x in selected[:n]])
                state['checkpoints'].append(entry);checkpoint()
            state.update(generation_closed=True,generation_closed_at=datetime.now(timezone.utc).isoformat(),status='all_candidates_frozen')
            checkpoint()
            # No writer call occurs after this point. Held gold is never sent to a model.
            if digest(BENCH/'held.json')!=plan['held_sha256']: raise ValueError('held manifest changed')
            held=json.loads((BENCH/'held.json').read_text())['tasks']
            state['held_opened']=True;state['status']='held_running';checkpoint()
            for entry in state['checkpoints']:
                if entry['status']!='frozen': continue
                root=BENCH/'checkpoints'/f"{entry['n']:02d}"
                for task in held:
                    if time.monotonic()-started>plan['overall_seconds']:
                        state['status']='stopped_overall_budget';checkpoint();return
                    if digest(root/'frozen.py')!=entry['sha256']: raise ValueError('frozen candidate changed')
                    result=fresh_evaluate(session,root/'frozen.py',task,root/'held'/task['task_id'],rows,generalized=True)
                    result['expected_empty']=not expected(task['parameters'],rows)
                    entry['held_results'].append(result);checkpoint()
                entry.update(status='evaluated',exact_correct=sum(x['exact_correct'] for x in entry['held_results']),
                             denominator=40,execution_ok=sum(x['execution_ok'] for x in entry['held_results']),
                             nonempty_correct=sum(x['exact_correct'] for x in entry['held_results'] if not x['expected_empty']),
                             nonempty_denominator=sum(not x['expected_empty'] for x in entry['held_results']),
                             format_ok=sum(x['format_ok'] for x in entry['held_results']))
                checkpoint()
            state['status']='complete_exploratory';checkpoint()
    except Exception as exc:
        state.update(status='stopped_error',error_type=type(exc).__name__,error=str(exc)[:500]);checkpoint()
        raise
    finally:
        state['elapsed_seconds']=round(time.monotonic()-started,2)
        state['finished_at']=datetime.now(timezone.utc).isoformat();checkpoint()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=('prepare','run','synthesize'))
    parser.add_argument('--request',type=Path)
    args=parser.parse_args()
    if args.mode=='prepare': print(json.dumps(prepare(),indent=2))
    elif args.mode=='run': run()
    else:
        request=json.loads(args.request.read_text())
        synthesize(URL,CONTRACT,request['examples'],Path(request['output']),allow_test_fixture=True)


if __name__=='__main__': main()
