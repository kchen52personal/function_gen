import json
from pathlib import Path
import pytest

import checkpoint_benchmark_v1 as bench
from synthesis import static_errors, build_synthesis_prompt


ROWS=[{'name':'Alpha','price':12.,'category':'gear'},
      {'name':'Beta','price':16.,'category':'gear'},
      {'name':'Other','price':5.,'category':'home'}]


def answer(params,items):
    return {'parameters':dict(params),'result':{'items':items},'source_url':bench.URL}


def test_grade_inclusive_filter_order_and_empty():
    p={'category_id':0,'max_price':16,'descending':False}
    expected=[{'name':'Alpha','price':12},{'name':'Beta','price':16}]
    assert bench.grade(answer(p,expected),p,ROWS)
    assert not bench.grade(answer(p,list(reversed(expected))),p,ROWS)
    assert not bench.grade(answer(p,expected[:1]),p,ROWS)
    p['max_price']=0
    assert bench.grade(answer(p,[]),p,ROWS)
    assert not bench.grade(answer(p,expected),p,ROWS)


def test_grade_rejects_wrong_price_name_types_and_parameter_echo():
    p={'category_id':0,'max_price':12,'descending':False}
    correct=[{'name':'Alpha','price':12}]
    for items in ([{'name':'Alpha','price':11}], [{'name':'Invented','price':12}],
                  [{'name':'Alpha','price':'12'}], [{'name':'Alpha','price':float('nan')}],
                  [{'name':'Alpha','price':True}]):
        assert not bench.grade(answer(p,items),p,ROWS)
    value=answer(p,correct);value['parameters']['category_id']=False
    assert not bench.grade(value,p,ROWS)
    value=answer(p,correct);value['parameters']['descending']=0
    assert not bench.grade(value,p,ROWS)


def test_catalog_oracle_matches_actual_fixture_and_change_is_detected():
    rows=bench.catalog()
    assert len(rows)==16
    p={'category_id':0,'max_price':75,'descending':False}
    gold=bench.expected(p,rows)
    changed=[dict(row,price=65) if row['name']=='Aluminum Laptop Stand' else row for row in rows]
    assert not bench.grade(answer(p,gold),p,changed)


def test_fixture_synthesis_opt_in_does_not_weaken_default():
    code='''async def site_function(page):
    await page.goto("http://127.0.0.1:18765/")
# playwright argparse output-dir
'''
    assert static_errors(code,[],bench.URL)
    assert not static_errors(code,[],bench.URL,allow_test_fixture=True)
    with pytest.raises(ValueError):
        static_errors(code,[],'http://127.0.0.1:9999/',allow_test_fixture=True)
    offsite=code.replace('127.0.0.1:18765','example.org')
    assert static_errors(offsite,[],bench.URL,allow_test_fixture=True)


def test_fixture_synthesis_prompt_has_no_public_https_conflict():
    examples=[{'task':{'parameters':{}},'script':'example one'},
              {'task':{'parameters':{}},'script':'example two'}]
    prompt,_=build_synthesis_prompt(bench.URL,bench.CONTRACT,examples,allow_test_fixture=True)
    assert 'must be an HTTP page on 127.0.0.1 port 18765' in prompt
    assert 'must be an HTTPS page' not in prompt


def test_result_parser_rejects_duplicate_results():
    with pytest.raises(ValueError): bench.parse_result('final result: {}\nfinal result: {}')
    with pytest.raises(ValueError): bench.parse_result('claimed success')


def test_all_nested_functions_frozen_before_any_held_execution(tmp_path,monkeypatch):
    project=tmp_path/'project';project.mkdir()
    root=project/'benchmark';root.mkdir()
    fixture=project/'fixture';(fixture/'dist/assets').mkdir(parents=True)
    asset=fixture/'dist/assets/index-7Zc1JihY.js';asset.write_text('fixture digest')
    dev=[bench.record(i,0,12,False) for i in range(1,9)]
    held=[bench.record(100+i,0,16,False) for i in range(40)]
    bench.save(root/'development.json',{'tasks':dev});bench.save(root/'held.json',{'tasks':held})
    bench.save(root/'contract.json',bench.CONTRACT)
    bench.save(root/'plan.json',{'overall_seconds':21600,
        'development_sha256':bench.digest(root/'development.json'),'held_sha256':bench.digest(root/'held.json'),
        'contract_sha256':bench.digest(root/'contract.json'),'fixture_asset_sha256':bench.digest(asset)})
    bench.save(root/'summary.json',{'status':'prepared','held_opened':False,'attempts':[],'checkpoints':[]})
    monkeypatch.setattr(bench,'PROJECT',project);monkeypatch.setattr(bench,'BENCH',root)
    monkeypatch.setattr(bench,'FIXTURE',fixture);monkeypatch.setattr(bench,'catalog',lambda:ROWS)
    class Session:
        def __enter__(self): return self
        def __exit__(self,*args): pass
    monkeypatch.setattr(bench,'FixtureSession',Session)
    prefix_sizes=[]
    def bounded(cmd,**kwargs):
        assert not json.loads((root/'summary.json').read_text())['held_opened']
        if 'synthesize' in cmd:
            request=json.loads(Path(cmd[cmd.index('--request')+1]).read_text())
            prefix_sizes.append(len(request['examples']))
            assert [x['task']['task_id'] for x in request['examples']]==[x['task_id'] for x in dev[:len(request['examples'])]]
            output=Path(request['output']);output.mkdir(parents=True)
            (output/'candidate.py').write_text('# generated candidate')
            bench.save(output/'summary.json',{'status':'candidate_ready'})
        else:
            workspace=project/cmd[cmd.index('--workspace-name')+1];workspace.mkdir()
            (workspace/'final_script.py').write_text('# submitted development script')
            output=project/'automated_runs'/cmd[cmd.index('--run-name')+1];output.mkdir(parents=True)
            bench.save(output/'summary.json',{'trajectory_status':'Submitted','process_exit':0})
        return 0,'',''
    monkeypatch.setattr(bench,'run_bounded',bounded)
    def evaluate(session,script,task,out,rows,*,generalized):
        if generalized:
            state=json.loads((root/'summary.json').read_text())
            assert state['generation_closed'] and state['held_opened']
            assert prefix_sizes==list(bench.SIZES)
            assert all(x.get('sha256') for x in state['checkpoints'])
        return {'exact_correct':True,'format_ok':True,'execution_ok':True,'process_exit':0}
    monkeypatch.setattr(bench,'fresh_evaluate',evaluate)
    bench.run()
    final=json.loads((root/'summary.json').read_text())
    assert final['status']=='complete_exploratory'
    assert all(x['exact_correct']==40 and x['denominator']==40 for x in final['checkpoints'])
