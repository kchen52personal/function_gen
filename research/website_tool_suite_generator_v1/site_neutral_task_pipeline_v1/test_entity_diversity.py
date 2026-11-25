import json
import pytest
import factory
from entity_diversity import normalize, identity_keys, validate_binding
from test_factory import proposal


def test_title_normalization_and_aliases_not_artist_names():
    item={'canonical_name':'Ｈｅｌｌｏ!','aliases':['你好'],'creator':'Artist One'}
    assert identity_keys(item)=={'hello','你好'}
    assert normalize('HeLLo ')==normalize('Ｈｅｌｌｏ!')
    validate_binding({'request':'Can you play 你好?'},item)
    with pytest.raises(ValueError):validate_binding({'request':'Play Artist One'},item)


def generate_1650(tmp_path):
    call=0
    def requester(prompt,*args):
        nonlocal call
        assert 'ENTITY DIVERSITY IS REQUIRED' in prompt
        values=[f'Play Title {call:02d}-{i:02d}' for i in range(8)]
        data=proposal('request',values,'Play requested media') if call==0 else {'cases':[{'request':v} for v in values]}
        data['identities']=[{'canonical_name':f'Title {call:02d}-{i:02d}','aliases':[],
                            'creator':'Some artist'} for i in range(8)]
        if call==0:
            data['cases'][0]={'request':'播放晴天'}
            data['identities'][0]={'canonical_name':'晴天','aliases':['Qing Tian'],'creator':'One'}
            data['cases'][1]={'request':'Play Qing Tian please'}
            data['identities'][1]={'canonical_name':'Qing Tian','aliases':['晴天'],'creator':'Other'}
        call+=1
        return json.dumps(data)
    _,summary=factory.create_taskset('https://example.org/','Play requested media',
        {'start_url':'https://example.org/','allowed_host':'example.org','pages':[]},tmp_path/'run',
        requester=requester,development_count=16,held_count=50,generation_batch_size=8,
        raw_request_argument=True,distinct_entities=True)
    return summary


def test_16_50_unique_groups_strip_generation_metadata_from_worker_inputs(tmp_path):
    summary=generate_1650(tmp_path)
    root=tmp_path/'run'
    catalog=json.loads((root/'request_catalog.json').read_text())
    assert len(catalog['development'])==16 and len(catalog['held_out'])==50
    assert summary['batch_generation']['calls'][0]['entity_duplicates_excluded']==1
    assert all(set(t)=={'task_id','parameters','intent'} for t in catalog['development']+catalog['held_out'])
    metadata=json.loads((root/'entity_metadata.json').read_text())
    keys=set()
    for item in metadata['identities'].values():
        assert not keys & identity_keys(item)
        keys.update(identity_keys(item))
    assert not metadata['catalog_verified']


def test_replacement_privately_rejects_held_target_under_new_wording(tmp_path):
    generate_1650(tmp_path)
    root=tmp_path/'run'
    catalog=json.loads((root/'request_catalog.json').read_text())
    metadata=json.loads((root/'entity_metadata.json').read_text())['identities']
    held_identity=metadata[catalog['held_out'][0]['task_id']]
    held_name=held_identity['canonical_name']
    def requester(prompt,*args):
        assert held_name not in prompt
        return json.dumps({'cases':[{'request':'Another wording for '+held_name},
                                    {'request':'Play Brand New Target 999'}],
            'identities':[held_identity,{'canonical_name':'Brand New Target 999','aliases':[],'creator':''}]})
    tasks=factory.propose_replacements('https://example.org/',
        json.loads((root/'contract.json').read_text()),catalog['development'],
        root/'reserved_fingerprints.json',2,1,tmp_path/'replacement',requester=requester)
    assert len(tasks)==1 and tasks[0]['parameters']['request']=='Play Brand New Target 999'


def test_distinct_mode_cannot_resume_old_wording_only_pool(tmp_path):
    with pytest.raises(ValueError,match='bounded task generation failed'):
        factory.create_taskset('https://example.org/','Play requested media',
            {'start_url':'https://example.org/','allowed_host':'example.org','pages':[]},tmp_path/'run',
            generation_batch_size=8,distinct_entities=True,generation_resume_checkpoint='old')
