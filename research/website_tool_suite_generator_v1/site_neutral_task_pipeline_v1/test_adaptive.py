import json

import adaptive
import factory
from codex_checkpoint import save_checkpoint

def proposal():
    return {'contract': {'goal':'Search the public catalog', 'boundary':'Read-only',
        'success_contract':'Return visible title', 'task_template':'Find {query}',
        'arguments':[{'name':'query','type':'string','description':'Search input'}],
        'returns':{'title':'str'}},
        'cases':[{'query':f'examplevalue{x:02d}'} for x in range(12)]}

def test_replacements_and_multiple_checkpoints_are_frozen_before_held(tmp_path,monkeypatch):
    output=tmp_path/'run'
    async def recon(site,out,max_pages):
        return {'start_url':site,'allowed_host':'example.org','pages':[{'http_status':200}]}
    monkeypatch.setattr(adaptive,'reconnaissance',recon)
    monkeypatch.setattr(adaptive,'create_taskset',lambda site,idea,rec,out:
        factory.create_taskset(site,idea,rec,out,requester=lambda *a:json.dumps(proposal())))
    def dev_run(site,contract,tasks,out,**kwargs):
        out.mkdir(parents=True)
        records=[]
        for task in tasks:
            script=out/task['task_id']/'qwen3.5-plus/agent/final_script.py'
            script.parent.mkdir(parents=True)
            script.write_text('# dev script')
            was_submitted = task['task_id'] in initial[:2] or task['task_id'].startswith('replacement_')
            records.append({'task_id':task['task_id'],'trajectory_status':'Submitted' if was_submitted else '',
                            'process_exit':0 if was_submitted else 124,
                            'script_sha256':adaptive.digest(script)})
        (out/'summary.json').write_text(json.dumps({'attempts':records}))
    monkeypatch.setattr(adaptive.development,'run',dev_run)
    def replacements(site,contract,prior,registry,count,round_number,out):
        assert len(prior)==8 and count==2 and round_number==1
        assert not json.loads((output/'summary.json').read_text())['held_opened']
        return [{'task_id':f'replacement_r1_{x:02d}','parameters':{'query':f'newvalue{x}'},
                 'intent':f'Find newvalue{x}'} for x in (1,2)]
    monkeypatch.setattr(adaptive,'propose_replacements',replacements)
    def synthesis(site,contract,examples,out):
        assert not json.loads((output/'summary.json').read_text())['held_opened']
        out.mkdir(parents=True)
        script=out/'candidate.py'
        script.write_text('# checkpoint '+str(len(examples)))
        return {'status':'candidate_ready','candidate_sha256':adaptive.digest(script)}
    monkeypatch.setattr(adaptive,'synthesize',synthesis)
    def assessment(site,contract,task,script,out):
        state=json.loads((output/'summary.json').read_text())
        if 'held_checks' in out.parts:
            assert state['generation_closed'] is True and state['held_opened'] is True
            assert [x['development_submission_count'] for x in state['checkpoints']]==[2,3,4]
            assert all(x.get('sha256') for x in state['checkpoints'])
        status = 'not_assessable' if 'development_checks' in out.parts and not 'checkpoints' in out.parts and \
            task['task_id'] not in initial[:2] and not task['task_id'].startswith('replacement_') else 'model_agreement'
        return {'task_id':task['task_id'],'status':status,'reason':'mock','format_valid':status=='model_agreement'}
    monkeypatch.setattr(adaptive,'evaluate_case',assessment)
    original=factory.create_taskset
    def create(site,idea,rec,out):
        contract,summary=original(site,idea,rec,out,requester=lambda *a:json.dumps(proposal()))
        initial.extend(x['task_id'] for x in json.loads((out/'development/tasks.json').read_text())['tasks'])
        return contract,summary
    initial=[]
    monkeypatch.setattr(adaptive,'create_taskset',create)
    state=adaptive.execute('https://example.org/','Search the catalog',output,max_rounds=1,target=4)
    assert state['status']=='complete_exploratory_checkpoint_comparison'
    assert state['development_attempt_count']==10
    assert state['machine_agreed_development_count']==4
    assert state['submitted_development_count']==4
    assert [x['held_model_agreements'] for x in state['checkpoints']]==[4,4,4]

def test_no_usable_development_never_opens_held(tmp_path, monkeypatch):
    async def recon(site,out,max_pages):
        return {'start_url':site,'allowed_host':'example.org','pages':[{'http_status':200}]}
    monkeypatch.setattr(adaptive,'reconnaissance',recon)
    monkeypatch.setattr(adaptive,'create_taskset',lambda site,idea,rec,out:
        factory.create_taskset(site,idea,rec,out,requester=lambda *a:json.dumps(proposal())))
    def empty_development(site,contract,tasks,out,**kwargs):
        assert kwargs['attempt_timeout'] == 1800
        assert kwargs['queue_timeout'] == 8 * (1800 + 40)
        out.mkdir(parents=True)
        (out/'summary.json').write_text(json.dumps({'attempts':[]}))
    monkeypatch.setattr(adaptive.development,'run',empty_development)
    def forbid_synthesis(*args):
        raise AssertionError('should not synthesize without development evidence')
    monkeypatch.setattr(adaptive,'synthesize',forbid_synthesis)
    state = adaptive.execute('https://example.org/','Search the catalog',tmp_path/'run',max_rounds=0,target=4)
    assert state['status'] == 'stopped_no_frozen_checkpoint'
    assert state['held_opened'] is False
    assert state['development_attempt_count'] == 8
    assert all(x['status'] == 'not_assessable' for x in state['development_attempts'])


def test_expired_batch_still_records_finished_attempts_without_new_work(tmp_path,monkeypatch):
    tick=[100.0]
    monkeypatch.setattr(adaptive.time,'monotonic',lambda:tick[0])
    async def recon(site,out,max_pages):
        return {'start_url':site,'allowed_host':'example.org','pages':[{'http_status':200}]}
    monkeypatch.setattr(adaptive,'reconnaissance',recon)
    monkeypatch.setattr(adaptive,'create_taskset',lambda site,idea,rec,out:
        factory.create_taskset(site,idea,rec,out,requester=lambda *a:json.dumps(proposal())))
    def development(site,contract,tasks,out,**kwargs):
        out.mkdir(parents=True);rows=[]
        for task in tasks[:2]:
            script=out/task['task_id']/'qwen3.5-plus/agent/final_script.py'
            script.parent.mkdir(parents=True);script.write_text('# submitted')
            rows.append({'task_id':task['task_id'],'process_exit':0,'trajectory_status':'Submitted',
                         'script_sha256':adaptive.digest(script)})
        (out/'summary.json').write_text(json.dumps({'status':'attempts_complete','attempts':rows}))
        tick[0]=10000.0
    monkeypatch.setattr(adaptive.development,'run',development)
    monkeypatch.setattr(adaptive,'synthesize',lambda *a: (_ for _ in ()).throw(AssertionError('must not synthesize')))
    state=adaptive.execute('https://example.org/','Search the catalog',tmp_path/'run',
        max_rounds=0,target=8,final_only=True,webwright_only=True,overall_seconds=600)
    assert state['status']=='stopped_budget_before_synthesis'
    assert state['webwright_submitted_attempt_count']==2
    assert len(state['development_attempts'])==2
    assert all(x['status']=='not_assessed_budget_exhausted' for x in state['development_attempts'])
    assert not state['held_opened'] and not state['checkpoints']

def test_access_failure_stops_without_replacements_or_opening_held(tmp_path,monkeypatch):
    async def recon(site,out,max_pages):
        return {'start_url':site,'allowed_host':'example.org','pages':[{'http_status':200}]}
    monkeypatch.setattr(adaptive,'reconnaissance',recon)
    monkeypatch.setattr(adaptive,'create_taskset',lambda site,idea,rec,out:
        factory.create_taskset(site,idea,rec,out,requester=lambda *a:json.dumps(proposal())))
    def failed(site,contract,tasks,out,**kwargs):
        out.mkdir(parents=True)
        (out/'summary.json').write_text(json.dumps({'status':'stopped_repeated_site_access_failures',
            'attempts':[{'task_id':tasks[0]['task_id'],'process_exit':126,'site_access_failure_signal':True}]}))
    monkeypatch.setattr(adaptive.development,'run',failed)
    monkeypatch.setattr(adaptive,'propose_replacements',lambda *a: (_ for _ in ()).throw(AssertionError('must stop')))
    state=adaptive.execute('https://example.org/','Search the catalog',tmp_path/'run',
                           max_rounds=5,target=8,final_only=True,webwright_only=True)
    assert state['status']=='stopped_repeated_site_access_failures'
    assert len(state['development_attempts'])==1 and state['submitted_development_count']==0
    assert state['held_opened'] is False

def test_final_only_requires_target_submissions_before_synthesis(tmp_path, monkeypatch):
    async def recon(site,out,max_pages):
        return {'start_url':site,'allowed_host':'example.org','pages':[{'http_status':200}]}
    monkeypatch.setattr(adaptive,'reconnaissance',recon)
    monkeypatch.setattr(adaptive,'create_taskset',lambda site,idea,rec,out:
        factory.create_taskset(site,idea,rec,out,requester=lambda *a:json.dumps(proposal())))
    def development(site,contract,tasks,out,**kwargs):
        out.mkdir(parents=True)
        records=[]
        for task in tasks[:7]:
            script=out/task['task_id']/'qwen3.5-plus/agent/final_script.py'
            script.parent.mkdir(parents=True);script.write_text('# source')
            records.append({'task_id':task['task_id'],'trajectory_status':'Submitted','process_exit':0,
                            'script_sha256':adaptive.digest(script)})
        (out/'summary.json').write_text(json.dumps({'attempts':records}))
    monkeypatch.setattr(adaptive.development,'run',development)
    monkeypatch.setattr(adaptive,'evaluate_case',lambda *a,**k:{'status':'inconclusive','format_valid':True})
    monkeypatch.setattr(adaptive,'synthesize',lambda *a: (_ for _ in ()).throw(AssertionError('must not synthesize')))
    state=adaptive.execute('https://example.org/','Search the catalog',tmp_path/'run',
                           max_rounds=0,target=8,final_only=True)
    assert state['submitted_development_count']==7
    assert state['status']=='stopped_insufficient_submitted_trajectories'
    assert state['checkpoints']==[] and state['held_opened'] is False

def test_final_only_produces_one_checkpoint_with_all_eight(tmp_path, monkeypatch):
    async def recon(site,out,max_pages):
        return {'start_url':site,'allowed_host':'example.org','pages':[{'http_status':200}]}
    monkeypatch.setattr(adaptive,'reconnaissance',recon)
    monkeypatch.setattr(adaptive,'create_taskset',lambda site,idea,rec,out:
        factory.create_taskset(site,idea,rec,out,requester=lambda *a:json.dumps(proposal())))
    def development(site,contract,tasks,out,**kwargs):
        out.mkdir(parents=True);records=[]
        for task in tasks:
            script=out/task['task_id']/'qwen3.5-plus/agent/final_script.py'
            script.parent.mkdir(parents=True);script.write_text('# source')
            records.append({'task_id':task['task_id'],'trajectory_status':'Submitted','process_exit':0,
                            'script_sha256':adaptive.digest(script)})
        (out/'summary.json').write_text(json.dumps({'attempts':records}))
    monkeypatch.setattr(adaptive.development,'run',development)
    def synthesize(site,contract,examples,out):
        assert len(examples)==8
        assert not json.loads((tmp_path/'run/summary.json').read_text())['held_opened']
        out.mkdir(parents=True);script=out/'candidate.py';script.write_text('# candidate')
        return {'status':'candidate_ready','candidate_sha256':adaptive.digest(script)}
    monkeypatch.setattr(adaptive,'synthesize',synthesize)
    monkeypatch.setattr(adaptive,'evaluate_case',lambda site,contract,task,script,out:
                        {'task_id':task['task_id'],'status':'inconclusive','format_valid':True})
    state=adaptive.execute('https://example.org/','Search the catalog',tmp_path/'run',
                           max_rounds=0,target=8,final_only=True)
    assert len(state['checkpoints'])==1 and state['checkpoints'][0]['development_submission_count']==8
    assert state['generation_closed'] and state['held_opened']
    assert len(state['checkpoints'][0]['held_results'])==4

def test_webwright_only_never_calls_independent_verifier(tmp_path, monkeypatch):
    async def recon(site,out,max_pages):
        return {'start_url':site,'allowed_host':'example.org','pages':[{'http_status':200}]}
    monkeypatch.setattr(adaptive,'reconnaissance',recon)
    monkeypatch.setattr(adaptive,'create_taskset',lambda site,idea,rec,out:
        factory.create_taskset(site,idea,rec,out,requester=lambda *a:json.dumps(proposal())))
    def development(site,contract,tasks,out,**kwargs):
        out.mkdir(parents=True);records=[]
        for task in tasks:
            script=out/task['task_id']/'qwen3.5-plus/agent/final_script.py'
            script.parent.mkdir(parents=True);script.write_text('# source')
            records.append({'task_id':task['task_id'],'trajectory_status':'Submitted','process_exit':0,
                            'script_sha256':adaptive.digest(script)})
        (out/'summary.json').write_text(json.dumps({'attempts':records}))
    monkeypatch.setattr(adaptive.development,'run',development)
    def synthesize(site,contract,examples,out):
        assert len(examples)==8
        assert all(x['assessment']['status']=='webwright_submitted' for x in examples)
        out.mkdir(parents=True);script=out/'candidate.py';script.write_text('# candidate')
        return {'status':'candidate_ready','candidate_sha256':adaptive.digest(script)}
    monkeypatch.setattr(adaptive,'synthesize',synthesize)
    monkeypatch.setattr(adaptive,'evaluate_case',lambda *a,**k: (_ for _ in ()).throw(AssertionError('independent verifier called')))
    monkeypatch.setattr(adaptive,'run_ungraded_case',lambda task,script,out:
                        {'task_id':task['task_id'],'status':'ran_ungraded','semantic_verdict':None})
    state=adaptive.execute('https://example.org/','Search the catalog',tmp_path/'run',
                           max_rounds=0,target=8,final_only=True,webwright_only=True)
    assert state['status']=='complete_held_outputs_ungraded'
    assert len(state['checkpoints'])==1 and state['generation_closed']
    assert all(x['status']=='ran_ungraded' for x in state['checkpoints'][0]['held_results'])
    assert 'held_model_agreements' not in state['checkpoints'][0]

def test_submissions_reach_synthesis_without_faking_evaluator_success(tmp_path,monkeypatch):
    async def recon(site,out,max_pages):
        return {'start_url':site,'allowed_host':'example.org','pages':[{'http_status':200}]}
    monkeypatch.setattr(adaptive,'reconnaissance',recon)
    monkeypatch.setattr(adaptive,'create_taskset',lambda site,idea,rec,out:
        factory.create_taskset(site,idea,rec,out,requester=lambda *a:json.dumps(proposal())))
    def dev_run(site,contract,tasks,out,**kwargs):
        out.mkdir(parents=True)
        records=[]
        for task in tasks[:2]:
            p=out/task['task_id']/'qwen3.5-plus/agent/final_script.py';p.parent.mkdir(parents=True);p.write_text('# draft')
            records.append({'task_id':task['task_id'],'trajectory_status':'Submitted','process_exit':0,
                            'script_sha256':adaptive.digest(p)})
        (out/'summary.json').write_text(json.dumps({'attempts':records}))
    monkeypatch.setattr(adaptive.development,'run',dev_run)
    def synthesis(site,contract,examples,out):
        assert [x['assessment']['status'] for x in examples] == ['fail','inconclusive']
        out.mkdir(parents=True);p=out/'candidate.py';p.write_text('# candidate')
        return {'status':'candidate_ready','candidate_sha256':adaptive.digest(p)}
    monkeypatch.setattr(adaptive,'synthesize',synthesis)
    initial=[]
    def assess(site,contract,task,script,out):
        if 'checkpoints' not in out.parts:
            initial.append(task['task_id'])
            return {'task_id':task['task_id'],'status':'fail' if len(initial)==1 else 'inconclusive',
                    'reason':'source assessment'}
        return {'task_id':task['task_id'],'status':'inconclusive','format_valid':True,'reason':'mock'}
    monkeypatch.setattr(adaptive,'evaluate_case',assess)
    state=adaptive.execute('https://example.org/','Search the catalog',tmp_path/'run',max_rounds=0,target=2)
    assert state['status']=='complete_exploratory_checkpoint_comparison'
    assert state['submitted_development_count']==2 and state['machine_agreed_development_count']==0
    assert state['checkpoints'][0]['held_model_agreements']==0
    assert len(state['checkpoints'][0]['held_results'])==4

def test_assessment_exception_is_inconclusive_not_pipeline_crash(tmp_path,monkeypatch):
    async def recon(site,out,max_pages):
        return {'start_url':site,'allowed_host':'example.org','pages':[{'http_status':200}]}
    monkeypatch.setattr(adaptive,'reconnaissance',recon)
    monkeypatch.setattr(adaptive,'create_taskset',lambda site,idea,rec,out:
        factory.create_taskset(site,idea,rec,out,requester=lambda *a:json.dumps(proposal())))
    def dev_run(site,contract,tasks,out,**kwargs):
        out.mkdir(parents=True)
        records=[]
        for task in tasks:
            script=out/task['task_id']/'qwen3.5-plus/agent/final_script.py'
            script.parent.mkdir(parents=True)
            script.write_text('# dev script')
            records.append({'task_id':task['task_id'],'trajectory_status':'Submitted','process_exit':0,
                            'script_sha256':adaptive.digest(script)})
        (out/'summary.json').write_text(json.dumps({'attempts':records}))
    monkeypatch.setattr(adaptive.development,'run',dev_run)
    def error(*args): raise ConnectionError('temporary failure')
    monkeypatch.setattr(adaptive,'evaluate_case',error)
    monkeypatch.setattr(adaptive,'synthesize',lambda *args: (_ for _ in ()).throw(ValueError('mock synth failure')))
    state=adaptive.execute('https://example.org/','Search the catalog',tmp_path/'run',max_rounds=0,target=4)
    assert state['status']=='stopped_no_frozen_checkpoint' and state['held_opened'] is False
    assert len(state['development_attempts'])==8
    assert state['submitted_development_count']==4
    assert all(x['status']=='inconclusive' and x['error_type']=='ConnectionError'
               for x in state['development_attempts'])

def test_batch_timeout_respects_remaining_overall_budget(tmp_path, monkeypatch):
    async def recon(site,out,max_pages):
        return {'start_url':site,'allowed_host':'example.org','pages':[{'http_status':200}]}
    monkeypatch.setattr(adaptive,'reconnaissance',recon)
    monkeypatch.setattr(adaptive,'create_taskset',lambda site,idea,rec,out:
        factory.create_taskset(site,idea,rec,out,requester=lambda *a:json.dumps(proposal())))
    calls=[]
    def empty_development(site,contract,tasks,out,**kwargs):
        calls.append(kwargs)
        out.mkdir(parents=True)
        (out/'summary.json').write_text(json.dumps({'attempts':[]}))
    monkeypatch.setattr(adaptive.development,'run',empty_development)
    state=adaptive.execute('https://example.org/','Search the catalog',tmp_path/'run',
                           max_rounds=0,target=4,overall_seconds=120)
    assert state['limits']['per_agent_seconds'] == 1800
    assert 0 < calls[0]['attempt_timeout'] <= 120
    assert 0 < calls[0]['queue_timeout'] <= 120

def test_timed_out_smoke_checkpoint_is_only_a_candidate_and_requires_integrity(tmp_path):
    task = {'task_id':'case_1'}
    attempt = tmp_path/'case_1/qwen3.5-plus'
    workspace = attempt/'agent'
    workspace.mkdir(parents=True)
    script = workspace/'final_script.py'
    script.write_text('# smoke-passing draft')
    sha = save_checkpoint(workspace,script)
    script.write_text('# later incomplete edit')
    candidate, origin = adaptive.select_development_candidate(tmp_path,task,{'process_exit':124})
    assert origin == 'unsubmitted_smoke_checkpoint'
    assert candidate.read_text() == '# smoke-passing draft'
    (attempt/'codex_checkpoints'/f'{sha}.py').write_text('# tampered')
    candidate, origin = adaptive.select_development_candidate(tmp_path,task,{'process_exit':124})
    assert candidate is None and origin == 'invalid_smoke_checkpoint'

def test_verified_mode_excludes_failures_replenishes_and_freezes_before_held(tmp_path, monkeypatch):
    output = tmp_path/'run'
    async def recon(site,out,max_pages):
        return {'start_url':site,'allowed_host':'example.org','pages':[{'http_status':200}]}
    monkeypatch.setattr(adaptive,'reconnaissance',recon)
    monkeypatch.setattr(adaptive,'create_taskset',lambda site,idea,rec,out:
        factory.create_taskset(site,idea,rec,out,requester=lambda *a:json.dumps(proposal())))
    initial = []
    def dev_run(site,contract,tasks,out,**kwargs):
        out.mkdir(parents=True); records=[]
        selected = tasks[:3] if not initial else tasks
        for task in selected:
            script=out/task['task_id']/'qwen3.5-plus/agent/final_script.py'
            script.parent.mkdir(parents=True); script.write_text('# source')
            records.append({'task_id':task['task_id'],'trajectory_status':'Submitted','process_exit':0,
                            'script_sha256':adaptive.digest(script)})
            if not initial and len(records)==3:
                initial.extend(x['task_id'] for x in selected)
        (out/'summary.json').write_text(json.dumps({'attempts':records}))
    monkeypatch.setattr(adaptive.development,'run',dev_run)
    def verify(site,contract,task,script,out):
        assert not json.loads((output/'summary.json').read_text())['held_opened']
        status = ('rejected' if task['task_id']==initial[0] else
                  'inconclusive' if task['task_id']==initial[1] else 'accepted')
        return {'task_id':task['task_id'],'status':status,'reason':'mock',
                'accepted_for_synthesis':status=='accepted','script_sha256':adaptive.digest(script)}
    monkeypatch.setattr(adaptive,'verify_case',verify)
    def replacements(site,contract,prior,registry,count,round_index,out):
        assert count==1
        return [{'task_id':'replacement_r1_01','parameters':{'query':'new-development'},'intent':'Find new-development'}]
    monkeypatch.setattr(adaptive,'propose_replacements',replacements)
    def synthesize(site,contract,examples,out):
        assert [x['task']['task_id'] for x in examples] == [initial[2], 'replacement_r1_01']
        assert all(x['assessment']['status']=='accepted' for x in examples)
        out.mkdir(parents=True); script=out/'candidate.py'; script.write_text('# candidate')
        return {'status':'candidate_ready','candidate_sha256':adaptive.digest(script)}
    monkeypatch.setattr(adaptive,'synthesize',synthesize)
    def held(site,contract,task,script,out):
        state=json.loads((output/'summary.json').read_text())
        assert state['generation_closed'] and state['held_opened']
        return {'task_id':task['task_id'],'status':'inconclusive'}
    monkeypatch.setattr(adaptive,'evaluate_case',held)
    state=adaptive.execute('https://example.org/','Search the catalog',output,max_rounds=1,
                           target=2,final_only=True,verify_development=True)
    assert state['webwright_submitted_attempt_count']==4
    assert state['accepted_development_count']==2
    assert state['development_selection']=='webwright_submission_and_independent_assessment'
    assert state['checkpoints'][0]['status']=='held_assessed_exploratory'
    assert len(state['checkpoints'][0]['held_results'])==4

def test_verified_mode_cannot_be_combined_with_submission_only(tmp_path):
    import pytest
    with pytest.raises(ValueError, match='cannot use webwright-only'):
        adaptive.execute('https://example.org/','Search the catalog',tmp_path/'run',
                         final_only=True,webwright_only=True,verify_development=True)
