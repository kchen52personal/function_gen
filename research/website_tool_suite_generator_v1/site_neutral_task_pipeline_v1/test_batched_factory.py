import json
import hashlib
import pytest
import factory
from test_factory import proposal


def recon():
    return {'start_url':'https://example.org/','allowed_host':'example.org','pages':[]}


def test_raw_schema_feedback_survives_timeout(tmp_path):
    prompts=[]
    def requester(prompt,*args):
        prompts.append(prompt)
        assert 'Each cases object must have exactly the key "request"' in prompt
        if len(prompts)==1:
            return json.dumps(proposal('query',['a','b'],'Search records'))
        if len(prompts)==2:
            raise TimeoutError('provider read timed out')
        assert 'Previous batch failed validation: ValueError: raw-request mode requires only request:string' in prompt
        return json.dumps(proposal('request',['a','b','c','d'],'Search records'))
    _,summary=factory.create_taskset('https://example.org/','Search records',recon(),tmp_path/'run',
        requester=requester,development_count=2,held_count=2,raw_request_argument=True,generation_batch_size=4)
    assert summary['batch_generation']['pool_count']==4


def test_repeated_timeouts_open_circuit_and_preserve_progress(tmp_path):
    calls=[]
    def requester(prompt,*args):
        calls.append(prompt)
        raise TimeoutError('provider read timed out')
    with pytest.raises(ValueError,match='bounded task generation failed'):
        factory.create_taskset('https://example.org/','Search records',recon(),tmp_path/'run',
            requester=requester,generation_batch_size=2)
    progress=json.loads((tmp_path/'run/generation_progress.json').read_text())
    assert len(calls)==4 and progress['status']=='stopped_repeated_api_timeouts'


def test_authorized_creation_allows_natural_language_seven_three(tmp_path):
    calls=[]
    def requester(prompt,*args):
        calls.append(prompt)
        if len(calls)==1:
            assert 'explicitly authorized item-creation' in prompt
            assert 'Vary natural-language phrasing' in prompt
            return json.dumps(proposal('request',[f'Create an item phrasing {i}' for i in range(8)],'Create a new item'))
        return json.dumps({'cases':[{'request':f'Another wording {i}'} for i in range(2)]})
    _,summary=factory.create_taskset('https://example.org/','Create a new item',recon(),tmp_path/'run',
        requester=requester,development_count=7,held_count=3,raw_request_argument=True,
        generation_batch_size=8,allow_authorized_creation=True)
    assert summary['development_count']==7 and summary['held_count']==3


def test_batched_generator_fills_exact_8_50_with_llm_inputs(tmp_path):
    calls=[]
    def requester(prompt,out,purpose):
        number=len(calls);calls.append(prompt)
        assert purpose=='site_neutral_task_generation_batch'
        assert factory.INPUT_DIVERSITY_GUIDANCE in prompt
        values=[f'Could you play unique song {number:02d}-{i:02d}' for i in range(8)]
        if number==0:
            assert 'exactly 8 distinct' in prompt
            assert 'complete natural-language user utterance' in prompt
            return json.dumps(proposal('request',values,'Play requested media'))
        assert 'FROZEN CONTRACT' in prompt and 'PRIOR UNSPLIT INPUTS' in prompt
        return json.dumps({'cases':[{'request':v} for v in values]})
    _,summary=factory.create_taskset('https://example.org/','Play requested media',recon(),
        tmp_path/'run',requester=requester,held_count=50,raw_request_argument=True,generation_batch_size=8)
    catalog=json.loads((tmp_path/'run/request_catalog.json').read_text())
    assert len(catalog['development'])==8 and len(catalog['held_out'])==50
    combined=catalog['development']+catalog['held_out']
    assert len({t['parameters']['request'] for t in combined})==58
    assert summary['batch_generation']['pool_count']==58
    assert len(calls)==8
    assert summary['batch_generation']['calls'][-1]['accepted']==2
    assert summary['batch_generation']['calls'][-1]['surplus_preserved_in_raw_log']==6
    saved=json.loads((tmp_path/'run/generation_pool.json').read_text())
    assert len(saved['parameters'])==58 and not saved['contains_expected_answers']
    assert 'Could you play unique song' in (tmp_path/'run/requests_so_far.txt').read_text()


def test_batched_generation_recovers_timeout_and_filters_duplicates(tmp_path):
    count=0
    def requester(prompt,*args):
        nonlocal count
        count+=1
        if count==1:raise TimeoutError('fake provider timeout')
        if count==2:return json.dumps(proposal('query',['one','two','three','four'],'Search records'))
        return json.dumps({'cases':[{'query':'one'},{'query':'one'},
                                   *[{'query':f'fresh-{count}-{i}'} for i in range(4)]]})
    _,summary=factory.create_taskset('https://example.org/','Search records',recon(),tmp_path/'run',
        requester=requester,generation_batch_size=4)
    assert summary['batch_generation']['pool_count']==12
    assert summary['batch_generation']['calls'][0]['error'].startswith('TimeoutError')
    assert summary['batch_generation']['calls'][2]['duplicates_excluded']==2


def test_later_batch_cannot_rewrite_contract(tmp_path):
    calls=0
    def requester(prompt,*args):
        nonlocal calls
        calls+=1
        if calls==1:return json.dumps(proposal('query',[f'q{i}' for i in range(4)],'Search records'))
        return json.dumps(proposal('other',[f'o{i}' for i in range(4)],'Unrelated capability'))
    with pytest.raises(ValueError,match='bounded task generation failed'):
        factory.create_taskset('https://example.org/','Search records',recon(),tmp_path/'run',
            requester=requester,generation_batch_size=4)
    progress=json.loads((tmp_path/'run/generation_progress.json').read_text())
    assert progress['status']=='failed_bounded_generation'
    assert progress['pool_count']==4 and calls==progress['maximum_calls']
    assert any('frozen contract' in e for e in progress['errors'])


@pytest.mark.parametrize('size',[-1,17,True])
def test_batch_size_validation(tmp_path,size):
    with pytest.raises(ValueError,match='batch size'):
        factory.create_taskset('https://example.org/','Search records',recon(),tmp_path/'run',
                               generation_batch_size=size)


def test_resume_preserves_57_inputs_and_asks_backend_for_only_one(tmp_path,monkeypatch):
    monkeypatch.setattr(factory,'PROJECT',tmp_path)
    source=tmp_path/'automated_runs/old';source.mkdir(parents=True)
    idea='Play requested media'
    data=proposal('request',[f'Please play unique track {i:03d}' for i in range(57)],idea)
    saved={'website':'https://example.org/','idea_sha256':hashlib.sha256(idea.encode()).hexdigest(),
           'seed':20261112,'raw_request_argument':True,'contract':data['contract'],
           'parameters':data['cases'],'split_status':'unsplit; not yet development/held labels'}
    checkpoint=source/'pool.json';checkpoint.write_text(json.dumps(saved))
    calls=[]
    def requester(prompt,*args):
        calls.append(prompt)
        assert 'Generate exactly 1 NEW' in prompt
        assert 'FROZEN CONTRACT' in prompt
        return json.dumps({'cases':[{'request':'Please play a different new target'}]})
    _,summary=factory.create_taskset('https://example.org/',idea,recon(),tmp_path/'new',requester=requester,
        generation_batch_size=8,held_count=50,raw_request_argument=True,generation_resume_checkpoint=checkpoint)
    assert len(calls)==1
    assert summary['batch_generation']['maximum_calls']==5
    assert summary['batch_generation']['resume_source']['resumed_input_count']==57
    pool=json.loads((tmp_path/'new/generation_pool.json').read_text())['parameters']
    assert pool[:57]==data['cases'] and len(pool)==58
