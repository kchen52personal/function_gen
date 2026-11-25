import json
from pathlib import Path
import pytest
import factory

def proposal(arg, values, goal):
    return {'contract': {'goal':goal, 'boundary':'Public read-only browsing',
        'success_contract':'Return visible result and source URL.',
        'task_template':'Find information for {' + arg + '}.',
        'arguments':[{'name':arg,'type':'string','description':'Input to the website function'}],
        'returns':{'title':'str','link':'url'}},
        'cases':[{arg:value} for value in values]}

@pytest.mark.parametrize(('url','idea','arg'), [
    ('https://example.org/', 'Search the catalog for books', 'book'),
    ('https://other.example/', 'Look up a place weather report', 'location'),
])
def test_same_factory_accepts_different_sites_and_function_shapes(tmp_path,url,idea,arg):
    values = [f'item{n:02d}' for n in range(12)]
    raw = json.dumps(proposal(arg, values, idea))
    recon = {'start_url':url, 'allowed_host':url.split('/')[2], 'pages':[]}
    contract, summary = factory.create_taskset(url, idea, recon, tmp_path/'split', requester=lambda *args: raw)
    dev = json.loads((tmp_path/'split/development/tasks.json').read_text())['tasks']
    held = json.loads((tmp_path/'split/held_out/tasks.json').read_text())['tasks']
    assert len(dev) == 8 and len(held) == 4
    assert not ({x['parameters'][arg] for x in dev} & {x['parameters'][arg] for x in held})
    assert contract['arguments'][0]['name'] == arg
    assert summary['held_given_to_codewriter'] is False

def test_rejects_duplicate_cases_and_unsafe_template():
    data = proposal('query', ['same']*12, 'Search the catalog')
    with pytest.raises(ValueError):
        factory.validate_proposal(data)
    data = proposal('query', [f'item{x}' for x in range(12)], 'Search the catalog')
    data['contract']['task_template'] = '{query.__class__}'
    with pytest.raises(ValueError):
        factory.validate_proposal(data)


def test_task_factory_requests_natural_language_and_no_extra_scope(tmp_path):
    data = proposal('query', [f'item{x:02d}' for x in range(12)], 'Search the catalog')
    def requester(prompt, *args):
        assert 'natural-language user request, not an invented URL' in prompt
        assert 'do not add pagination, filters' in prompt
        assert factory.INPUT_DIVERSITY_GUIDANCE in prompt
        return json.dumps(data)
    factory.create_taskset('https://example.org/', 'Search the catalog',
                           {'start_url': 'https://example.org/', 'allowed_host': 'example.org', 'pages': []},
                           tmp_path/'run', requester=requester)


def test_factory_eight_development_fifty_held_with_media_permission(tmp_path):
    data=proposal('query',[f'song{x:03d}' for x in range(58)],'Search and play first song')
    def requester(prompt,*args):
        assert 'exactly 58 distinct' in prompt
        assert 'Transient media playback is explicitly authorized' in prompt
        assert 'ONE read-only parameterized' not in prompt
        return json.dumps(data)
    _,summary=factory.create_taskset('https://media.example/','Search and play first song',
        {'start_url':'https://media.example/','allowed_host':'media.example','pages':[]},tmp_path/'run',
        requester=requester,allow_media_playback=True,held_count=50)
    dev=json.loads((tmp_path/'run/development/tasks.json').read_text())['tasks']
    held=json.loads((tmp_path/'run/held_out/tasks.json').read_text())['tasks']
    assert len(dev)==8 and len(held)==50
    assert not ({t['parameters']['query'] for t in dev}&{t['parameters']['query'] for t in held})
    assert summary['held_count']==50 and summary['allow_media_playback']


@pytest.mark.parametrize('count',[0,101,True])
def test_held_count_is_bounded(tmp_path,count):
    with pytest.raises(ValueError,match='held count'):
        factory.create_taskset('https://example.org/','Search records',{},tmp_path/'run',held_count=count)

def test_private_url_and_boolean_integer_rejected():
    with pytest.raises(ValueError):
        factory.validate_site('https://127.0.0.1/', 'Search the website')
    assert not factory.valid_arg(True, 'integer')

def test_contract_must_not_reveal_held_string():
    with pytest.raises(ValueError):
        factory.ensure_contract_does_not_reveal_held_values({'goal':'search for bananas'}, [{'parameters':{'q':'bananas'}}])

def test_split_swaps_contract_collision_into_development_without_leaking():
    tasks=[{'task_id':f't{i}', 'parameters':{'q':f'item{i:02d}'}} for i in range(12)]
    contract={'goal':'search for item08'}
    dev,held,swaps=factory.split_avoiding_contract_leaks(tasks,contract)
    assert (len(dev),len(held),swaps)==(8,4,1)
    assert any(x['task_id']=='t8' for x in dev)
    factory.ensure_contract_does_not_reveal_held_values(contract,held)
    assert len({x['task_id'] for x in dev+held})==12

def test_split_fails_closed_when_too_few_safe_held_cases():
    tasks=[{'task_id':f't{i}', 'parameters':{'q':f'item{i:02d}'}} for i in range(12)]
    contract={'goal':' '.join(f'item{i:02d}' for i in range(9))}
    with pytest.raises(ValueError,match='not enough cases safe'):
        factory.split_avoiding_contract_leaks(tasks,contract)

def test_retries_bad_case_keys_with_actionable_feedback(tmp_path):
    good = proposal('query', [f'item{x:02d}' for x in range(12)], 'Search the catalog')
    bad = json.loads(json.dumps(good))
    bad['cases'] = [{'argument_name': f'item{x:02d}'} for x in range(12)]
    prompts = []
    def requester(prompt, *args):
        prompts.append(prompt)
        return json.dumps(bad if len(prompts) == 1 else good)
    _, summary = factory.create_taskset('https://example.org/', 'Search the catalog',
        {'start_url':'https://example.org/', 'allowed_host':'example.org', 'pages':[]},
        tmp_path/'split', requester=requester)
    assert summary['development_count'] == 8
    assert 'case must contain exactly the declared parameters' in prompts[1]
    assert 'literal string "argument_name"' in prompts[0]

@pytest.mark.parametrize('placeholder', ['field_name', 'meaningful_output_name'])
def test_retries_placeholder_return_name(tmp_path, placeholder):
    good = proposal('query', [f'item{x:02d}' for x in range(12)], 'Search the catalog')
    bad = json.loads(json.dumps(good))
    bad['contract']['returns'] = {placeholder:'list[object]'}
    prompts = []
    def requester(prompt, *args):
        prompts.append(prompt)
        return json.dumps(bad if len(prompts) == 1 else good)
    contract, summary = factory.create_taskset('https://example.org/', 'Search the catalog',
        {'start_url':'https://example.org/', 'allowed_host':'example.org', 'pages':[]},
        tmp_path/'split', requester=requester)
    assert contract['returns'] == good['contract']['returns']
    assert summary['development_count'] == 8
    assert 'template placeholder' in prompts[1]

def test_replacement_excludes_held_duplicate_without_sending_held_values(tmp_path):
    url = 'https://example.org/'
    raw = json.dumps(proposal('query', [f'uniqueitem{x:02d}' for x in range(12)], 'Search the catalog'))
    contract, _ = factory.create_taskset(url, 'Search the catalog',
        {'start_url':url, 'allowed_host':'example.org', 'pages':[]}, tmp_path/'split', requester=lambda *a:raw)
    dev = json.loads((tmp_path/'split/development/tasks.json').read_text())['tasks']
    held = json.loads((tmp_path/'split/held_out/tasks.json').read_text())['tasks']
    held_query = held[0]['parameters']['query']
    def requester(prompt, *args):
        assert all(item['parameters']['query'] not in prompt for item in held)
        assert factory.INPUT_DIVERSITY_GUIDANCE in prompt
        return json.dumps({'cases':[{'query':held_query},{'query':'new-unseen-value'}]})
    cases = factory.propose_replacements(url,contract,dev,tmp_path/'split/reserved_fingerprints.json',
                                        2,1,tmp_path/'replacements',requester=requester)
    assert len(cases) == 1 and cases[0]['parameters']['query'] == 'new-unseen-value'
    assert json.loads((tmp_path/'replacements/summary.json').read_text())['exact_duplicates_excluded'] == 1

def test_task_generation_recovers_from_unexpected_provider_error(tmp_path):
    class ProviderError(Exception): pass
    attempts = []
    raw = json.dumps(proposal('query', [f'item{x:02d}' for x in range(12)], 'Search the catalog'))
    def requester(*args):
        attempts.append(1)
        if len(attempts) == 1:
            raise ProviderError('provider unavailable')
        return raw
    url = 'https://example.org/'
    _, summary = factory.create_taskset(url, 'Search the catalog',
        {'start_url':url,'allowed_host':'example.org','pages':[]},tmp_path/'split',requester=requester)
    assert len(attempts) == 2 and summary['development_count'] == 8


def test_input_language_is_preserved_without_claiming_entity_disjointness():
    values = ['晴天', '周杰伦 晴天', '晴天 live', 'HELLO', 'Hello!', 'hello world',
              '歌曲 café', '你好 世界', 'a-b', 'a b', ' spaced query ', '日本語']
    _, tasks = factory.validate_proposal(proposal('query', values, 'Search the catalog'))
    assert [t['parameters']['query'] for t in tasks] == values
    assert all(t['parameters']['query'] in t['intent'] for t in tasks)


def test_raw_requests_are_inputs_not_preextracted_queries(tmp_path):
    data=proposal('request',[f'Please play song number {i}' for i in range(12)],'Play requested media')
    def requester(prompt,*args):
        assert 'complete natural-language user utterance' in prompt
        assert 'Keep each case concise' in prompt
        return json.dumps(data)
    contract,summary=factory.create_taskset('https://example.org/','Play requested media',
        {'start_url':'https://example.org/','allowed_host':'example.org','pages':[]},
        tmp_path/'run',requester=requester,raw_request_argument=True)
    catalog=json.loads((tmp_path/'run/request_catalog.json').read_text())
    assert summary['raw_request_argument'] and not catalog['contains_expected_answers']
    assert len(catalog['development'])==8 and len(catalog['held_out'])==4
    assert contract['arguments'][0]['name']=='request'


def test_raw_request_mode_rejects_already_extracted_query_contract(tmp_path):
    data=proposal('query',[f'song number {i}' for i in range(12)],'Play media')
    with pytest.raises(ValueError,match='bounded task generation failed'):
        factory.create_taskset('https://example.org/','Play requested media',
            {'start_url':'https://example.org/','allowed_host':'example.org','pages':[]},
            tmp_path/'run',requester=lambda *args:json.dumps(data),raw_request_argument=True)
