import json
import pytest

import factory


@pytest.mark.parametrize('kind,value',[('array',[{'product':'name','quantity':2}]),
    ('object',{'enabled':True,'items':[1,2]}),('list[string]',['name']),
    ('list[integer]',[1,2]),('list[object]',[{'name':'item'}])])
def test_supported_structured_types_roundtrip(kind,value):
    assert factory.valid_arg(value,kind)
    assert json.loads(factory.cli_value(value))==value


@pytest.mark.parametrize('kind,value',[('array',[1]*21),('object',{'x':float('nan')}),
    ('list[integer]',[True]),('list[string]',[4]),('array','not a list'),
    ('object',{'x':{'x':{'x':{'x':{'x':1}}}}})])
def test_structured_types_remain_bounded(kind,value):
    assert not factory.valid_arg(value,kind)


def test_nested_held_strings_are_not_silently_exempt():
    task={'parameters':{'initial_cart':[{'product_name':'secret-held-product','quantity':2}]}}
    with pytest.raises(ValueError):
        factory.ensure_contract_does_not_reveal_held_values({'goal':'use secret-held-product'},[task])


def test_structured_proposals_keep_disjoint_whole_input_combinations(tmp_path):
    contract={'goal':'Modify a populated demo cart','boundary':'Cart only, no checkout',
        'success_contract':'Remove requested item and update another quantity via UI controls',
        'task_template':'Starting with {initial_cart}, remove {remove_item} and set {update_item} to {quantity}',
        'arguments':[{'name':'initial_cart','type':'array','description':'Initial product lines'},
                     {'name':'remove_item','type':'string','description':'Product to remove'},
                     {'name':'update_item','type':'string','description':'Distinct product to update'},
                     {'name':'quantity','type':'integer','description':'New positive quantity'}],
        'returns':{'cart_lines':'list[object]','total':'number'}}
    cases=[{'initial_cart':[{'product_name':f'product{i:02d}','quantity':1}],
            'remove_item':f'remove{i:02d}','update_item':f'update{i:02d}','quantity':i+1} for i in range(12)]
    _,meta=factory.create_taskset('https://example.org/','Modify the cart',
        {'start_url':'https://example.org/','allowed_host':'example.org','pages':[]},tmp_path/'run',
        requester=lambda *args:json.dumps({'contract':contract,'cases':cases}))
    assert meta['development_count']==8 and meta['held_count']==4
    dev=json.loads((tmp_path/'run/development/tasks.json').read_text())['tasks']
    held=json.loads((tmp_path/'run/held_out/tasks.json').read_text())['tasks']
    assert not ({json.dumps(t['parameters'],sort_keys=True) for t in dev}&
                {json.dumps(t['parameters'],sort_keys=True) for t in held})
