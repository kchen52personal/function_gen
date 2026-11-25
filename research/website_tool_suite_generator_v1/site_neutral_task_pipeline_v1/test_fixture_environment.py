import json
import pytest

import adaptive
import factory
import fixture_environment
from fixture_environment import IsolatedFixtureEnvironment, URL
from run_fixture_end_to_end import shadow_review
from test_factory import proposal
from runtime.docker_fixture_shell import command_budget
from synthesis import build_synthesis_prompt


def test_command_budget_is_bounded_and_legacy_default_unchanged():
    assert command_budget()==110
    assert command_budget('900')==900
    with pytest.raises(ValueError):command_budget('99999')


def test_observed_ui_vocabulary_is_not_a_secret_held_combination():
    context={'description':'Choose Gear or Apparel'}
    tasks=[{'parameters':{'category':'Gear','quantity':1}}]
    factory.ensure_contract_does_not_reveal_held_values(context,tasks,public_values={'Gear'})
    with pytest.raises(ValueError):
        factory.ensure_contract_does_not_reveal_held_values(context,tasks)
    with pytest.raises(ValueError):
        factory.ensure_contract_does_not_reveal_held_values({'description':'private-query'},
            [{'parameters':{'query':'private-query'}}],public_values={'Gear'})


def test_cart_permissions_cannot_escape_fixture_or_contradict_synthesis():
    examples=[{'script':'first'},{'script':'second'}]
    contract={'returns':{'cart':'list[object]','total':'number'}}
    with pytest.raises(ValueError):
        build_synthesis_prompt('https://example.org/',contract,examples,allow_fixture_cart=True)
    prompt,_=build_synthesis_prompt(URL,contract,examples,allow_test_fixture=True,allow_fixture_cart=True)
    assert 'cart changes and promo application are permitted' in prompt
    assert 'do not log in, change site state' not in prompt
    assert 'never log in, check out, purchase' in prompt


def test_cart_factory_prompt_permissions_and_observed_label_exemption(tmp_path):
    data=proposal('category',['Gear']*12,'Build the requested demo cart')
    data['contract']['arguments'].append({'name':'quantity','type':'integer','description':'Requested count'})
    data['contract']['task_template']='Build cart for {category} with quantity {quantity}'
    data['contract']['boundary']='Use the category Gear; cart changes only, never place an order'
    data['cases']=[{'category':'Gear','quantity':i+1} for i in range(12)]
    recon={'start_url':URL,'allowed_host':'127.0.0.1','pages':[{'frames':[{'actions':[{'text':'Gear'}]}]}]}
    def request(prompt,*args):
        assert 'ONE cart-building parameterized browser function' in prompt
        assert 'Cart quantity changes and promo-code application are explicitly allowed' in prompt
        assert 'public read-only function' not in prompt
        return json.dumps(data)
    _,meta=factory.create_taskset(URL,'Build a demo cart',recon,tmp_path/'run',requester=request,
        allow_test_fixture=True,allow_fixture_cart=True)
    assert meta['public_ui_vocabulary_exemptions']==['Gear']
    assert meta['held_count']==4 and meta['development_count']==8


def test_cart_factory_default_public_mode_cannot_be_enabled(tmp_path):
    with pytest.raises(ValueError,match='explicit isolated'):
        factory.create_taskset('https://example.org/','Build a cart',{},tmp_path/'run',allow_fixture_cart=True)


@pytest.mark.parametrize('cart',[False,True])
def test_actual_adapter_emits_consistent_permissions(tmp_path,monkeypatch,cart):
    monkeypatch.setattr(fixture_environment,'PROJECT',tmp_path)
    env=IsolatedFixtureEnvironment(tmp_path/'run',allow_cart=cart)
    task={'task_id':'task_001','intent':'Build the specified cart','parameters':{'quantity':2}}
    def bounded(cmd,**kwargs):
        assert ('--allow-fixture-cart' in cmd) is cart
        spec=json.loads(open(cmd[cmd.index('--task-file')+1]).read())
        assert ('Cart changes and promo application are allowed' in spec['task']) is cart
        assert ('Do not modify cart or checkout.' in spec['task']) is (not cart)
        workspace=tmp_path/cmd[cmd.index('--workspace-name')+1];workspace.mkdir()
        (workspace/'final_script.py').write_text('# emitted by fake WebWright')
        out=tmp_path/'automated_runs'/cmd[cmd.index('--run-name')+1];out.mkdir(parents=True)
        (out/'summary.json').write_text(json.dumps({'process_exit':0,'trajectory_status':'Submitted'}))
        return 0,'',''
    monkeypatch.setattr(fixture_environment,'run_bounded',bounded)
    path=env.run_development(URL,{'success_contract':'Return final cart'},[task],tmp_path/'batch',
                             attempt_timeout=1800,queue_timeout=1800)
    state=json.loads((path/'summary.json').read_text())
    assert state['attempts'][0]['trajectory_status']=='Submitted'


def test_fixture_task_factory_is_opt_in_and_fixed_url_only(tmp_path):
    data=json.dumps(proposal('query',[f'input{x:02d}' for x in range(12)],'Read the isolated catalog'))
    recon={'start_url':URL,'allowed_host':'127.0.0.1','pages':[]}
    with pytest.raises(ValueError):
        factory.create_taskset(URL,'Read the catalog',recon,tmp_path/'denied')
    with pytest.raises(ValueError):
        factory.create_taskset('http://127.0.0.1:9999/','Read the catalog',recon,tmp_path/'wrong',allow_test_fixture=True)
    _,summary=factory.create_taskset(URL,'Read the catalog',recon,tmp_path/'allowed',
        requester=lambda *args:data,allow_test_fixture=True)
    assert (summary['development_count'],summary['held_count'])==(8,4)


def test_environment_cannot_enable_other_pipeline_modes(tmp_path):
    with pytest.raises(ValueError,match='submission-only'):
        adaptive.execute(URL,'Read the catalog',tmp_path/'run',environment=object())


def test_local_pipeline_freezes_after_eight_without_verifier_votes(tmp_path,monkeypatch):
    root=tmp_path/'run'
    class Environment:
        name='mock_fixture'
        validate_site=IsolatedFixtureEnvironment.validate_site
        async def reconnaissance(self,url,out,max_pages):
            return {'start_url':url,'allowed_host':'127.0.0.1','pages':[{'http_status':200}]}
        def create_taskset(self,url,idea,recon,out):
            return factory.create_taskset(url,idea,recon,out,allow_test_fixture=True,
                requester=lambda *args:json.dumps(proposal('query',[f'input{x:02d}' for x in range(12)],idea)))
        def run_development(self,url,contract,tasks,out,**kwargs):
            out.mkdir(parents=True)
            rows=[]
            for task in tasks:
                script=out/task['task_id']/'qwen3.5-plus/agent/final_script.py'
                script.parent.mkdir(parents=True);script.write_text('# deliberately not independently graded')
                rows.append({'task_id':task['task_id'],'process_exit':0,'trajectory_status':'Submitted',
                             'script_sha256':adaptive.digest(script)})
            (out/'summary.json').write_text(json.dumps({'attempts':rows}))
        def synthesize(self,url,contract,examples,out):
            assert len(examples)==8
            assert all(x['assessment']['status']=='webwright_submitted' for x in examples)
            assert not json.loads((root/'summary.json').read_text())['held_opened']
            out.mkdir(parents=True);(out/'candidate.py').write_text('# frozen mock')
            return {'status':'candidate_ready','candidate_sha256':adaptive.digest(out/'candidate.py')}
        def run_ungraded_case(self,task,script,out):
            state=json.loads((root/'summary.json').read_text())
            assert state['generation_closed'] and state['held_opened']
            return {'task_id':task['task_id'],'status':'ran_ungraded','semantic_verdict':None}
    def forbidden(*args,**kwargs):raise AssertionError('No verifier can vote in this mode')
    monkeypatch.setattr(adaptive,'verify_case',forbidden)
    monkeypatch.setattr(adaptive,'evaluate_case',forbidden)
    state=adaptive.execute(URL,'Read the catalog',root,environment=Environment(),
        webwright_only=True,final_only=True,max_rounds=0)
    assert state['status']=='complete_held_outputs_ungraded'
    assert state['submitted_development_count']==8
    assert len(state['checkpoints'])==1
    assert len(state['checkpoints'][0]['held_results'])==4


def test_visual_gate_never_accepts_unbound_webwright_evidence(tmp_path):
    class Environment:
        name='mock_fixture'
        validate_site=IsolatedFixtureEnvironment.validate_site
        async def reconnaissance(self,url,out,max_pages):
            return {'start_url':url,'allowed_host':'127.0.0.1','pages':[{'http_status':200}]}
        def create_taskset(self,url,idea,recon,out):
            return factory.create_taskset(url,idea,recon,out,allow_test_fixture=True,
                requester=lambda *args:json.dumps(proposal('query',[f'input{x:02d}' for x in range(12)],idea)))
        def run_development(self,url,contract,tasks,out,**kwargs):
            out.mkdir(parents=True)
            rows=[]
            for task in tasks:
                script=out/task['task_id']/'qwen3.5-plus/agent/final_script.py'
                script.parent.mkdir(parents=True); script.write_text('# submitted')
                rows.append({'task_id':task['task_id'],'process_exit':0,'trajectory_status':'Submitted',
                             'script_sha256':adaptive.digest(script)})
            (out/'summary.json').write_text(json.dumps({'attempts':rows}))
        def synthesize(self,*args):
            raise AssertionError('unbound evidence must not reach synthesis')
        def run_ungraded_case(self,*args):
            raise AssertionError('held tasks must remain sealed')
    state=adaptive.execute(URL,'Read the catalog',tmp_path/'run',environment=Environment(),
        webwright_only=True,final_only=True,max_rounds=0,visual_verification=True)
    assert state['status']=='stopped_insufficient_accepted_development'
    assert state['submitted_development_count']==8
    assert not state['held_opened'] and not state['checkpoints']
    assert all(x['status']=='inconclusive' for x in state['development_attempts'])


def test_visual_gate_does_not_accept_script_written_evidence(tmp_path,monkeypatch):
    import visual_verifier
    root=tmp_path/'run'
    class Environment:
        name='mock_fixture'
        validate_site=IsolatedFixtureEnvironment.validate_site
        async def reconnaissance(self,url,out,max_pages):
            return {'start_url':url,'allowed_host':'127.0.0.1','pages':[{'http_status':200}]}
        def create_taskset(self,url,idea,recon,out):
            return factory.create_taskset(url,idea,recon,out,allow_test_fixture=True,
                requester=lambda *args:json.dumps(proposal('query',[f'input{x:02d}' for x in range(12)],idea)))
        def run_development(self,url,contract,tasks,out,**kwargs):
            out.mkdir(parents=True);rows=[]
            for task in tasks:
                script=out/task['task_id']/'qwen3.5-plus/agent/final_script.py'
                script.parent.mkdir(parents=True);script.write_text('# submitted')
                rows.append({'task_id':task['task_id'],'process_exit':0,'trajectory_status':'Submitted',
                             'script_sha256':adaptive.digest(script)})
            (out/'summary.json').write_text(json.dumps({'attempts':rows}))
        def run_development_verification(self,task,script,out):
            assert not json.loads((root/'summary.json').read_text())['held_opened']
            return self.run_ungraded_case(task,script,out)
        def synthesize(self,url,contract,examples,out):
            assert all(x['assessment']['status']=='pass' for x in examples)
            assert not json.loads((root/'summary.json').read_text())['held_opened']
            out.mkdir(parents=True); (out/'candidate.py').write_text('# frozen')
            return {'status':'candidate_ready','candidate_sha256':adaptive.digest(out/'candidate.py')}
        def run_ungraded_case(self,task,script,out):
            work=out/'workspace'; (work/'execution/screenshots').mkdir(parents=True)
            (work/'candidate.py').write_bytes(script.read_bytes())
            (work/'execution/screenshots/after.png').write_bytes(b'stub')
            (work/'execution/final_script_log.txt').write_text('final result: '+json.dumps({'result':{}}))
            return {'task_id':task['task_id'],'process_exit':0,'status':'ran_ungraded'}
    monkeypatch.setattr(visual_verifier,'judge',lambda *a,**k:{'status':'pass','format':{'status':'pass'}})
    state=adaptive.execute(URL,'Read the catalog',root,environment=Environment(),
        webwright_only=True,final_only=True,max_rounds=0,visual_verification=True)
    assert state['status']=='stopped_insufficient_accepted_development'
    assert state.get('accepted_development_count',0)==0 and not state['held_opened']
    assert len(state['development_attempts'])==8
    assert all(x['status']=='inconclusive' and x['diagnostic_status']=='pass' and
               not x['accepted_for_synthesis'] for x in state['development_attempts'])


def test_shadow_failure_cannot_change_selection_or_open_held(tmp_path):
    root=tmp_path/'run';(root/'tasks/development').mkdir(parents=True)
    task={'task_id':'task_001','intent':'Read the catalog','parameters':{'q':'example'}}
    (root/'tasks/development/tasks.json').write_text(json.dumps({'tasks':[task]}))
    summary={'status':'stopped_insufficient_submitted_trajectories','held_opened':False,'contract':{},
             'submitted_development_count':1,'development_attempts':[
                 {'round':0,'task_id':'task_001','status':'webwright_submitted'}]}
    summary['contract']={'goal':'Read products'}
    path=root/'development_rounds/round_00/task_001/qwen3.5-plus/agent/final_runs/run_1'
    path.mkdir(parents=True);(path/'final_script_log.txt').write_text('final result: {}')
    (root/'summary.json').write_text(json.dumps(summary))
    report=shadow_review(root,judge=lambda *args,**kwargs:{'status':'fail'})
    assert report['reviews'][0]['status']=='fail'
    assert report['pipeline_summary_unchanged']
    assert json.loads((root/'summary.json').read_text())==summary
