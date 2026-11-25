from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import hashlib
import json
import subprocess
import threading
import pytest
import adaptive
import factory
import public_environment
from local_propose import _append_jsonl
from event_log import emit
from public_environment import IsolatedPublicEnvironment
from test_factory import proposal

RECON={'start_url':'https://example.org/','allowed_host':'example.org','pages':[]}


def _write_ledger_rows(path, worker):
    for n in range(20):
        _append_jsonl(path, {'worker':worker, 'n':n, 'payload':'x'*16000})


def test_multi_process_ledger_appends_preserve_whole_json_records(tmp_path):
    path = tmp_path/'ledger.jsonl'
    with ProcessPoolExecutor(max_workers=7) as pool:
        list(pool.map(_write_ledger_rows, [path]*7, range(7)))
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 140
    assert {(r['worker'], r['n']) for r in rows} == {(w,n) for w in range(7) for n in range(20)}


def test_seven_development_workers_run_concurrently_and_preserve_selection(tmp_path):
    barrier = threading.Barrier(7)
    active = set()
    max_active = []
    lock = threading.Lock()
    class Env(IsolatedPublicEnvironment):
        def _run_development_sequential(self, url, contract, tasks, output, **kwargs):
            task = tasks[0]
            output.mkdir(parents=True, exist_ok=False)
            with lock:
                active.add(task['task_id'])
                max_active.append(len(active))
            barrier.wait(timeout=8)
            script = output/task['task_id']/'qwen3.5-plus/agent/final_script.py'
            script.parent.mkdir(parents=True)
            script.write_text('# submitted '+task['task_id'])
            digest = hashlib.sha256(script.read_bytes()).hexdigest()
            (output/'summary.json').write_text(json.dumps({'attempts':[{
                'task_id':task['task_id'], 'process_exit':0,
                'trajectory_status':'Submitted', 'script_sha256':digest}]}))
            with lock:
                active.remove(task['task_id'])
            return output
    env = Env(tmp_path, 'https://example.org/', development_workers=7)
    tasks = [{'task_id':f'task_{n:03d}'} for n in range(7)]
    output = tmp_path/'round'
    env.run_development('https://example.org/', {}, tasks, output, attempt_timeout=180, queue_timeout=180)
    summary = json.loads((output/'summary.json').read_text())
    assert max(max_active) == 7
    assert summary['development_workers'] == 7
    assert [r['task_id'] for r in summary['attempts']] == [t['task_id'] for t in tasks]
    assert summary['status'] == 'attempts_complete'
    for task, row in zip(tasks, summary['attempts']):
        selected, origin = adaptive.select_development_candidate(output, task, row)
        assert origin == 'submitted' and selected.read_text() == '# submitted '+task['task_id']


def test_parallel_worker_failure_preserves_other_attempts_and_fails_closed(tmp_path):
    class Env(IsolatedPublicEnvironment):
        def _run_development_sequential(self, url, contract, tasks, output, **kwargs):
            task = tasks[0]
            if task['task_id'] == 'bad':
                raise RuntimeError('simulated failure')
            output.mkdir(parents=True, exist_ok=False)
            (output/task['task_id']).mkdir()
            (output/'summary.json').write_text(json.dumps({'attempts':[{'task_id':task['task_id']}]}))
            return output
    env = Env(tmp_path, 'https://example.org/', development_workers=2)
    output = tmp_path/'round'
    with pytest.raises(RuntimeError, match='parallel development workers failed'):
        env.run_development('https://example.org/', {}, [{'task_id':'ok'}, {'task_id':'bad'}], output,
                            attempt_timeout=180, queue_timeout=180)
    state = json.loads((output/'summary.json').read_text())
    assert state['status'] == 'failed'
    assert [x['task_id'] for x in state['attempts']] == ['ok']
    assert state['parallel_errors'] == [{'task_id':'bad', 'error_type':'RuntimeError'}]


def test_worker_bounds_and_task_id_validation(tmp_path):
    for bad in (0, 8, True):
        with pytest.raises(ValueError, match='development_workers'):
            IsolatedPublicEnvironment(tmp_path, 'https://example.org/', development_workers=bad)
    env = IsolatedPublicEnvironment(tmp_path, 'https://example.org/', development_workers=2)
    with pytest.raises(ValueError, match='unique simple task IDs'):
        env.run_development('https://example.org/', {}, [{'task_id':'../bad'}, {'task_id':'x'}], tmp_path/'round',
                            attempt_timeout=180, queue_timeout=180)

def test_concurrent_timeline_lines_are_complete_and_unique(tmp_path):
    path=tmp_path/'events.jsonl'
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i:emit(path,'test','tick',index=i),range(80)))
    rows=[json.loads(x) for x in path.read_text().splitlines()]
    assert len(rows)==len({r['event_id'] for r in rows})==80
    assert {r['index'] for r in rows}==set(range(80))
    assert all(r['utc'].endswith('+00:00') and r['monotonic_ns']>0 for r in rows)


def test_early_split_frozen_before_more_generation_and_no_relabel(tmp_path):
    root=tmp_path/'run';calls=0;frozen=[]
    def ready(contract,tasks):
        assert calls==2 and len(tasks)==4
        assert all(set(t)=={'task_id','parameters','intent'} for t in tasks)
        frozen.extend(tasks)
        assert not (root/'held_out/tasks.json').exists()
        saved=json.loads((root/'generation_pool.json').read_text())
        assert saved['split_status'].startswith('development prefix frozen')
    def requester(prompt,*args):
        nonlocal calls
        if calls>=2:
            assert frozen
            assert 'never browser outcomes' in prompt
        values=[f'Play independent title {calls}-{i}' for i in range(2)]
        value=proposal('request',values,'Play requested media') if calls==0 else {'cases':[{'request':v} for v in values]}
        value['identities']=[{'canonical_name':f'independent title {calls}-{i}','aliases':[],'creator':''} for i in range(2)]
        calls+=1
        return json.dumps(value)
    _,summary=factory.create_taskset('https://example.org/','Play requested media',RECON,root,
        requester=requester,development_count=4,held_count=4,generation_batch_size=2,
        raw_request_argument=True,distinct_entities=True,on_development_ready=ready)
    catalog=json.loads((root/'request_catalog.json').read_text())
    assert catalog['development']==frozen
    assert len(catalog['held_out'])==4 and calls==4
    assert summary['held_contract_collision_swaps']==0
    assert 'never reshuffled' in (root/'development_freeze.json').read_text()


def test_checked_distinct_checkpoint_resume_then_freeze(tmp_path,monkeypatch):
    monkeypatch.setattr(factory,'PROJECT',tmp_path)
    path=tmp_path/'automated_runs/old/generation_pool.json';path.parent.mkdir(parents=True)
    idea='Play requested media';value=proposal('request',['Play Title One','Play Title Two'],idea)
    data={'website':'https://example.org/','idea_sha256':hashlib.sha256(idea.encode()).hexdigest(),
          'seed':20261112,'raw_request_argument':True,'contract':value['contract'],'parameters':value['cases'],
          'identities':[{'canonical_name':n,'aliases':[],'creator':''} for n in ('Title One','Title Two')],
          'split_status':'unsplit; not yet development/held labels'}
    path.write_text(json.dumps(data));frozen=[]
    def requester(prompt,*args):
        assert len(frozen)==2
        return json.dumps({'cases':[{'request':'Play Title Three'},{'request':'Play Title Four'}],
            'identities':[{'canonical_name':n,'aliases':[],'creator':''} for n in ('Title Three','Title Four')]})
    factory.create_taskset(data['website'],idea,RECON,tmp_path/'new',requester=requester,
        generation_batch_size=2,development_count=2,held_count=2,raw_request_argument=True,
        distinct_entities=True,generation_resume_checkpoint=path,
        on_development_ready=lambda contract,tasks:frozen.extend(tasks))
    assert [t['parameters'] for t in frozen]==value['cases']
    # A pool marked as used for development must not be reshuffled into tests.
    data['split_status']='development prefix frozen; remaining inputs reserved for held'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='bounded task generation failed'):
        factory.create_taskset(data['website'],idea,RECON,tmp_path/'bad',requester=requester,
            generation_batch_size=2,development_count=2,held_count=2,raw_request_argument=True,
            distinct_entities=True,generation_resume_checkpoint=path)


def test_prestarted_worker_consumed_once_and_cancel_joined(tmp_path):
    started=threading.Event();calls=[]
    class Env(IsolatedPublicEnvironment):
        def _run_development_actual(self,*args,**kwargs):
            calls.append(args);started.set()
            assert self._cancel.wait(3)
            return 'saved partial attempt'
    env=Env(tmp_path,'https://example.org/',generation_batch_size=2,parallel_generation_development=True)
    args=('https://example.org/',{'contract':1},[{'task_id':'one'}],tmp_path/'round')
    env.prestart_development(*args,attempt_timeout=10,queue_timeout=20)
    assert started.wait(3)
    with pytest.raises(ValueError,match='changed'):
        env.run_development(*args[:2],[{'task_id':'wrong'}],args[-1],attempt_timeout=10,queue_timeout=20)
    env.close()
    assert env.run_development(*args,attempt_timeout=10,queue_timeout=20)=='saved partial attempt'
    assert len(calls)==1


def test_drain_preserves_prestarted_development(tmp_path):
    started=threading.Event();release=threading.Event()
    class Env(IsolatedPublicEnvironment):
        def _run_development_actual(self,*args,**kwargs):
            started.set()
            assert release.wait(3)
            assert not self._cancel.is_set()
            return 'completed bounded batch'
    env=Env(tmp_path,'https://example.org/',generation_batch_size=2,parallel_generation_development=True)
    args=('https://example.org/',{'contract':1},[{'task_id':'one'}],tmp_path/'round')
    env.prestart_development(*args,attempt_timeout=10,queue_timeout=20)
    assert started.wait(3)
    release.set()
    env.close(drain_development=True)
    assert env.run_development(*args,attempt_timeout=10,queue_timeout=20)=='completed bounded batch'


def test_held_timeout_saves_partial_output_and_workspace(tmp_path,monkeypatch):
    work=tmp_path/'work';work.mkdir();(work/'evidence.txt').write_text('partial evidence')
    monkeypatch.setattr(public_environment,'workspace',lambda:work)
    env=IsolatedPublicEnvironment(tmp_path,'https://example.org/')
    def fail(*args,**kwargs):
        raise subprocess.TimeoutExpired('bounded command',180,output=b'partial stdout',stderr=b'partial stderr')
    monkeypatch.setattr(env,'run_command',fail)
    script=tmp_path/'frozen.py';script.write_text('# frozen')
    result=env.run_ungraded_case({'task_id':'one','parameters':{'query':'test'}},script,tmp_path/'case')
    assert result['process_exit']==124 and result['semantic_verdict'] is None
    assert (tmp_path/'case/workspace/evidence.txt').read_text()=='partial evidence'
    assert (tmp_path/'case/stdout.log').read_text()=='partial stdout'
    assert json.loads((tmp_path/'case/failure_diagnostics.json').read_text())['stop_signals']==['wall_timeout']


def test_adaptive_overlap_reuses_worker_then_freezes_before_held(tmp_path):
    root=tmp_path/'run';worker_started=threading.Event();attempts=[]
    class Env(IsolatedPublicEnvironment):
        async def reconnaissance(self,url,out,max_pages):
            return {**RECON,'pages':[{'http_status':200}]}
        def create_taskset(self,url,idea,recon,out,**kwargs):
            calls=0
            def requester(prompt,*args):
                nonlocal calls
                if calls==2:
                    assert worker_started.wait(3), 'generation did not overlap development'
                    assert not (root/'tasks/held_out/tasks.json').exists()
                values=[f'Independent query {calls}-{i}' for i in range(2)]
                value=proposal('query',values,idea) if calls==0 else {'cases':[{'query':v} for v in values]}
                calls+=1
                return json.dumps(value)
            return factory.create_taskset(url,idea,recon,out,requester=requester,
                generation_batch_size=2,**kwargs)
        def _run_development_actual(self,url,contract,tasks,out,**kwargs):
            out.mkdir(parents=True);rows=[];worker_started.set()
            for task in tasks:
                attempts.append(task['task_id'])
                script=out/task['task_id']/'qwen3.5-plus/agent/final_script.py'
                script.parent.mkdir(parents=True);script.write_text('# mock')
                rows.append({'task_id':task['task_id'],'trajectory_status':'Submitted',
                    'process_exit':0,'script_sha256':adaptive.digest(script)})
            (out/'summary.json').write_text(json.dumps({'attempts':rows}))
            return out
        def synthesize(self,url,contract,examples,out):
            assert len(examples)==4
            assert not json.loads((root/'summary.json').read_text())['held_opened']
            out.mkdir(parents=True);(out/'candidate.py').write_text('# frozen mock')
            return {'status':'candidate_ready','candidate_sha256':adaptive.digest(out/'candidate.py')}
        def run_ungraded_case(self,task,script,out):
            state=json.loads((root/'summary.json').read_text())
            assert state['generation_closed'] and state['held_opened']
            return {'task_id':task['task_id'],'status':'ran_ungraded','semantic_verdict':None}
    env=Env(root,'https://example.org/',generation_batch_size=2,parallel_generation_development=True)
    try:
        state=adaptive.execute('https://example.org/','Search records',root,target=4,
            development_count=4,held_count=4,final_only=True,webwright_only=True,environment=env)
    finally:env.close()
    assert len(attempts)==len(set(attempts))==4
    assert state['status']=='complete_held_outputs_ungraded'
    assert len(state['checkpoints'][0]['held_results'])==4
    events=[json.loads(line) for line in (root/'events.jsonl').read_text().splitlines()]
    assert any(r['event']=='parallel_worker_awaited' for r in events)
