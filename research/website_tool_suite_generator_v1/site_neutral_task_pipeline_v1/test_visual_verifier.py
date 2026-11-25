import json
import pytest
import visual_verifier as v


def test_legacy_and_standard_log_markers():
    value={'parameters':{'q':'x'},'result':{'items':[{'name':'x'}]},'source_url':'https://example.org/'}
    assert v.parse_result_log('final result: '+json.dumps(value)) == (value,False)
    assert v.parse_result_log('step 2 action: final result: '+json.dumps(value)) == (value,True)
    with pytest.raises(ValueError): v.parse_result_log('no result')


def test_unsubstantiated_or_unknown_image_cannot_pass():
    value={'requirements':[{'requirement':'correct items','verdict':'pass','evidence':[
        {'image_id':'invented','observation':'items match'}]}]}
    assert v.normalize_judgment(value,{'image_1'})['status']=='inconclusive'
    value['requirements'][0]['evidence'][0]['image_id']='image_1'
    assert v.normalize_judgment(value,{'image_1'})['status']=='pass'
    value['requirements'][0]['verdict']='fail'
    assert v.normalize_judgment(value,{'image_1'})['status']=='fail'


def test_mixed_valid_and_invented_citations_are_not_silently_accepted():
    value={'requirements':[{'requirement':'correct result','verdict':'pass','evidence':[
        {'image_id':'image_1','observation':'Header visible'},
        {'image_id':'invented','observation':'Requested value confirmed'}]}]}
    assert v.normalize_judgment(value,{'image_1'})['status']=='inconclusive'
    value['requirements'][0]['verdict']='fail'
    assert v.normalize_judgment(value,{'image_1'})['status']=='inconclusive'


def test_link_text_alone_cannot_verify_destination():
    value={'requirements':[{'requirement':'Check the title and link','verdict':'pass','evidence':[
        {'image_id':'image_1','observation':'Title and arXiv:1234 visible'}]}]}
    urls=v.candidate_result_urls({'result':{'items':[{'link':'https://example.org/a'}]}})
    assert v.normalize_judgment(value,{'image_1'},urls)['status']=='inconclusive'
    value['requirements'][0]['evidence'][0]['observation']='Visible: https://example.org/attacker'
    assert v.normalize_judgment(value,{'image_1'},urls)['status']=='inconclusive'
    value['requirements'][0]['evidence'][0]['observation']='Visible destination: https://example.org/a'
    assert v.normalize_judgment(value,{'image_1'},urls)['status']=='pass'


def test_multi_url_and_omitted_link_requirement_cannot_pass():
    urls=['https://example.org/a','https://example.org/b']
    value={'requirements':[{'requirement':'Check links','verdict':'pass','evidence':[
        {'image_id':'image_1','observation':'https://example.org/a'}]}]}
    assert v.normalize_judgment(value,{'image_1'},urls)['status']=='inconclusive'
    value['requirements'][0]['evidence'][0]['observation'] += ' https://example.org/b'
    assert v.normalize_judgment(value,{'image_1'},urls)['status']=='pass'
    value['requirements'][0]['requirement']='Check titles'
    assert v.normalize_judgment(value,{'image_1'},urls)['status']=='inconclusive'


def test_url_traversal_limit_never_silently_skips_results(tmp_path):
    with pytest.raises(ValueError):
        v.candidate_result_urls({'result':{'items':['x']*100+['https://example.org/hidden']}})
    nested='https://example.org/hidden'
    for _ in range(10): nested={'next':nested}
    with pytest.raises(ValueError):v.candidate_result_urls({'result':nested})
    image=tmp_path/'screen.png';image.write_bytes(b'\x89PNG\r\n\x1a\n'+b'test')
    candidate={'parameters':{},'result':{'items':['x']*100+['https://example.org/hidden']},
               'source_url':'https://example.org/'}
    def forbidden(*args):raise AssertionError('model should not be called')
    report=v.judge('https://example.org/',{}, {'task_id':'one','parameters':{}},candidate,[image],
                   tmp_path/'out',requester=forbidden)
    assert report['status']=='inconclusive' and report['error_type']=='ValueError'


def test_missing_only_evidence_cannot_support_definite_failure():
    value={'requirements':[{'requirement':'due date','verdict':'fail','evidence':[
        {'image_id':'image_1','observation':'Due date: NOT DISPLAYED'}]}]}
    assert v.normalize_judgment(value,{'image_1'})['status']=='inconclusive'
    value['requirements'][0]['evidence'].append({'image_id':'image_2','observation':'Due date: Monday'})
    assert v.normalize_judgment(value,{'image_1','image_2'})['status']=='fail'
    value['requirements'][0]['evidence']=[{'image_id':'image_1','observation':'时间未显示'}]
    assert v.normalize_judgment(value,{'image_1'})['status']=='inconclusive'


@pytest.mark.parametrize('observation',[
    'The section name is not visible', 'Price is hidden', 'The state is unavailable',
    'The next page is obscured', '日期不可见', '名称未显示', '结果被遮挡',
])
def test_missing_evidence_rule_is_not_specific_to_one_task(observation):
    value={'requirements':[{'requirement':'a requested fact','verdict':'fail',
                            'evidence':[{'image_id':'image_1','observation':observation}]}]}
    assert v.normalize_judgment(value,{'image_1'})['status']=='inconclusive'


def test_mixed_claim_in_one_citation_is_conservatively_inconclusive():
    # The text guard cannot parse a mixed contradictory + missing observation;
    # it deliberately loses a fail verdict rather than risk an unsupported one.
    value={'requirements':[{'requirement':'correct item and price','verdict':'fail',
        'evidence':[{'image_id':'image_1','observation':'Item is the wrong model; price not displayed'}]}]}
    assert v.normalize_judgment(value,{'image_1'})['status']=='inconclusive'


def test_before_state_alone_cannot_establish_outcome():
    value={'requirements':[{'requirement':'item added','verdict':'fail',
        'evidence':[{'image_id':'image_1','observation':'Cart has no items'}]}]}
    roles={'image_1':'before','image_2':'after'}
    assert v.normalize_judgment(value,{'image_1'},image_roles={'image_1':'before'})['status']=='inconclusive'
    assert v.normalize_judgment(value,set(roles),image_roles=roles)['status']=='inconclusive'
    value['requirements'][0]['evidence'].append({'image_id':'image_2','observation':'Cart unavailable'})
    assert v.normalize_judgment(value,set(roles),image_roles=roles)['status']=='inconclusive'
    value['requirements'][0]['evidence'][1]['observation']='Cart has no items after action'
    assert v.normalize_judgment(value,set(roles),image_roles=roles)['status']=='fail'


def test_invalid_capture_role_is_rejected(tmp_path):
    image=tmp_path/'image.png';image.write_bytes(b'\x89PNG\r\n\x1a\n' + b'test')
    with pytest.raises(ValueError): v._image_parts([image],['invented'])
    with pytest.raises(ValueError): v._image_parts([image],['before','after'])


def test_numeric_diagnostics_are_bounded_and_site_neutral():
    contract={'goal':'Return at most three entries sorted by price ascending, within max_price.'}
    task={'parameters':{'max_price':20}}
    result={'result':{'items':[{'price':8},{'price':12},{'price':21}]}}
    checks=[{'op':'count_at_most','path':'result.items','value':3,'source_quote':'at most three'},
            {'op':'sorted_ascending','path':'result.items','field':'price','source_quote':'price ascending'},
            {'op':'numeric_lte','path':'result.items','field':'price','parameter':'max_price','source_quote':'within max_price'},
            {'op':'count_equals','path':'result.__class__','value':2,'source_quote':'at most three'}]
    assert [x['status'] for x in v.numeric_diagnostics(checks,contract,task,result)] == ['pass','pass','fail','inconclusive']


def test_numeric_diagnostics_accept_safe_equivalent_model_fields():
    contract={'goal':'Return exactly three entries sorted by price ascending.'}
    task={'parameters':{}}
    result={'result':{'items':[{'price':8},{'price':12},{'price':21}]}}
    checks=[{'op':'count_equals','path':'result.items','count':3,'source_quote':'exactly three'},
            {'op':'sorted_ascending','path':'result.items.price','source_quote':'price ascending'}]
    assert [x['status'] for x in v.numeric_diagnostics(checks,contract,task,result)] == ['pass','pass']


def test_model_pass_and_schema_failure_are_separate(tmp_path):
    import base64
    # Minimal PNG signature is sufficient for the bounded transport test; no model reads it.
    image=tmp_path/'screen.png'; image.write_bytes(b'\x89PNG\r\n\x1a\n' + b'test')
    contract={'goal':'Show items','returns':{'field_name':'list[object]'}}
    task={'task_id':'one','parameters':{'q':'x'}}
    result={'parameters':{'q':'x'},'result':{'items':[{'name':'x'}]},'source_url':'https://example.org/'}
    def requester(payload,out):
        assert payload['messages'][0]['content'][2]['image_url']['url'].startswith('data:image/png;base64,')
        return {'model':'fake','choices':[{'message':{'content':json.dumps({'requirements':[
            {'requirement':'returned item','verdict':'pass','evidence':[{'image_id':'image_1','observation':'item x'}]}]})}}]}
    answer=v.judge('https://example.org/',contract,task,result,[image],tmp_path/'out',requester=requester)
    assert answer['status']=='pass' and answer['format']['status']=='fail'
    assert answer['is_ground_truth'] is False


def test_judge_failure_is_inconclusive(tmp_path):
    image=tmp_path/'screen.png';image.write_bytes(b'\x89PNG\r\n\x1a\n' + b'test')
    task={'task_id':'one','parameters':{}}
    result={'parameters':{},'result':{'title':'x'},'source_url':'https://example.org/'}
    def broken(*args): raise ConnectionError('not available')
    answer=v.judge('https://example.org/',{'returns':{'title':'str'}},task,result,[image],tmp_path/'out',requester=broken)
    assert answer['status']=='inconclusive' and answer['error_type']=='ConnectionError'
    assert answer['elapsed_seconds'] >= 0


def test_oversized_input_fails_closed_without_calling_model(tmp_path):
    image=tmp_path/'screen.png';image.write_bytes(b'\x89PNG\r\n\x1a\n' + b'test')
    task={'task_id':'one','parameters':{},'intent':'x'*(v.MAX_TEXT+1)}
    result={'parameters':{},'result':{'title':'x'},'source_url':'https://example.org/'}
    def forbidden(*args): raise AssertionError('must not judge truncated task')
    answer=v.judge('https://example.org/',{'returns':{'title':'str'}},task,result,[image],tmp_path/'out',requester=forbidden)
    assert answer['status']=='inconclusive' and 'exceeds' in answer['reason']


@pytest.mark.parametrize('ids,expected',[
    (['R1'],'inconclusive'),(['R1','R1'],'inconclusive'),(['R1','R3'],'inconclusive'),
    (['R1','R2'],'pass'),
])
def test_precomputed_checklist_requires_exact_coverage(tmp_path,ids,expected):
    image=tmp_path/'screen.png';image.write_bytes(b'\x89PNG\r\n\x1a\n'+b'test')
    task={'task_id':'one','parameters':{},'intent':'Report name and date'}
    result={'parameters':{},'result':{'answer':'x'},'source_url':'https://example.org/'}
    checks=[{'id':'R1','requirement':'Check name'},{'id':'R2','requirement':'Check date'}]
    response={'requirements':[{'id':x,'requirement':{'R1':'Check name','R2':'Check date'}.get(x,'other'),'verdict':'pass',
        'evidence':[{'image_id':'image_1','observation':'Fact visible'}]} for x in ids]}
    requester=lambda *args: {'choices':[{'message':{'content':json.dumps(response)}}]}
    report=v.judge('https://example.org/',{'returns':{'answer':'str'}},task,result,[image],tmp_path/'out',
                   requester=requester,required_checks=checks)
    assert report['status']==expected


def test_checklist_id_cannot_be_reused_for_different_requirement(tmp_path):
    image=tmp_path/'screen.png';image.write_bytes(b'\x89PNG\r\n\x1a\n'+b'test')
    task={'task_id':'one','parameters':{}}
    result={'parameters':{},'result':{'answer':'x'},'source_url':'https://example.org/'}
    checks=[{'id':'R1','requirement':'Check the date'}]
    response={'requirements':[{'id':'R1','requirement':'Check an unrelated title','verdict':'pass',
                              'evidence':[{'image_id':'image_1','observation':'Title visible'}]}]}
    requester=lambda *args: {'choices':[{'message':{'content':json.dumps(response)}}]}
    report=v.judge('https://example.org/',{'returns':{'answer':'str'}},task,result,[image],tmp_path/'out',
                   requester=requester,required_checks=checks)
    assert report['status']=='inconclusive' and 'text' in report['reason']


def test_required_action_evidence_cannot_be_replaced_by_after_image(tmp_path):
    action=tmp_path/'action.png';action.write_bytes(b'\x89PNG\r\n\x1a\n'+b'action')
    after=tmp_path/'after.png';after.write_bytes(b'\x89PNG\r\n\x1a\n'+b'after')
    task={'task_id':'one','parameters':{},'intent':'Click the requested button'}
    result={'parameters':{},'result':{'answer':'done'},'source_url':'https://example.org/'}
    checks=[{'id':'R1','requirement':'Check the requested action','roles':['action','after']}]
    response={'requirements':[{'id':'R1','requirement':'Check the requested action','verdict':'pass',
        'evidence':[{'image_id':'image_2','observation':'After state visible'}]}]}
    requester=lambda *args:{'choices':[{'message':{'content':json.dumps(response)}}]}
    args=('https://example.org/',{'returns':{'answer':'str'}},task,result,[action,after])
    report=v.judge(*args,tmp_path/'out',requester=requester,image_roles=['action','after'],required_checks=checks)
    assert report['status']=='inconclusive' and report['coverage']['missing_evidence_roles']=={'R1':['action']}
    response['requirements'][0]['evidence'].append({'image_id':'image_1','observation':'Action point visible'})
    report=v.judge(*args,tmp_path/'out2',requester=requester,image_roles=['action','after'],required_checks=checks)
    assert report['status']=='pass' and report['coverage']['missing_evidence_roles']=={}


def test_missing_required_role_does_not_call_model(tmp_path):
    after=tmp_path/'after.png';after.write_bytes(b'\x89PNG\r\n\x1a\n'+b'after')
    task={'task_id':'one','parameters':{}}
    result={'parameters':{},'result':{'answer':'done'},'source_url':'https://example.org/'}
    def forbidden(*args):raise AssertionError('model must not be called')
    report=v.judge('https://example.org/',{'returns':{'answer':'str'}},task,result,[after],tmp_path/'out',
        requester=forbidden,image_roles=['after'],
        required_checks=[{'id':'R1','requirement':'Check action','roles':['action']}])
    assert report['status']=='inconclusive' and 'role' in report['reason']


def test_specific_action_image_cannot_be_replaced_by_another_action_image(tmp_path):
    images=[]
    for i in range(3):
        path=tmp_path/f'{i}.png';path.write_bytes(b'\x89PNG\r\n\x1a\n'+bytes([i]));images.append(path)
    task={'task_id':'one','parameters':{}}
    result={'parameters':{},'result':{'answer':'done'},'source_url':'https://example.org/'}
    checks=[{'id':'R1','requirement':'Check second action','images':['image_2','image_3']}]
    response={'requirements':[{'id':'R1','requirement':'Check second action','verdict':'pass',
        'evidence':[{'image_id':'image_1','observation':'First action visible'},
                    {'image_id':'image_3','observation':'Final state visible'}]}]}
    requester=lambda *args:{'choices':[{'message':{'content':json.dumps(response)}}]}
    report=v.judge('https://example.org/',{'returns':{'answer':'str'}},task,result,images,tmp_path/'out',
                   requester=requester,image_roles=['action','action','after'],required_checks=checks)
    assert report['status']=='inconclusive' and report['coverage']['missing_evidence_images']=={'R1':['image_2']}


def test_missing_id_requires_unique_exact_requirement_text(tmp_path):
    image=tmp_path/'screen.png';image.write_bytes(b'\x89PNG\r\n\x1a\n'+b'test')
    task={'task_id':'one','parameters':{}}
    result={'parameters':{},'result':{'answer':'x'},'source_url':'https://example.org/'}
    checks=[{'id':'R1','requirement':'Check exact title'}]
    response={'requirements':[{'requirement':'Check exact title','verdict':'pass',
                               'evidence':[{'image_id':'image_1','observation':'Title visible'}]}]}
    request=lambda *args:{'choices':[{'message':{'content':json.dumps(response)}}]}
    report=v.judge('https://example.org/',{'returns':{'answer':'str'}},task,result,[image],tmp_path/'out',
                   requester=request,required_checks=checks)
    assert report['status']=='pass' and report['coverage']['inferred_ids']==['R1']
    response['requirements'][0]['requirement']='Check title'
    report=v.judge('https://example.org/',{'returns':{'answer':'str'}},task,result,[image],tmp_path/'out2',
                   requester=request,required_checks=checks)
    assert report['status']=='inconclusive'
    response['requirements'][0]['requirement']='Check exact title'
    ambiguous=checks+[{'id':'R2','requirement':'Check exact title'}]
    report=v.judge('https://example.org/',{'returns':{'answer':'str'}},task,result,[image],tmp_path/'out3',
                   requester=request,required_checks=ambiguous)
    assert report['status']=='inconclusive'


def test_conflicting_numerical_check_blocks_a_visual_pass(tmp_path):
    image=tmp_path/'screen.png'; image.write_bytes(b'\x89PNG\r\n\x1a\n' + b'test')
    contract={'goal':'Return items sorted by price ascending.', 'returns':{'items':'list[object]'}}
    task={'task_id':'one','parameters':{'q':'x'}}
    result={'parameters':{'q':'x'},'result':{'items':[{'price':9},{'price':3}]},'source_url':'https://example.org/'}
    response={'requirements':[{'requirement':'correct items','verdict':'pass','evidence':[
        {'image_id':'image_1','observation':'two items visible'}]}],
        'numeric_checks':[{'op':'sorted_ascending','path':'result.items.price','source_quote':'price ascending'}]}
    requester=lambda *args: {'choices':[{'message':{'content':json.dumps(response)}}]}
    report=v.judge('https://example.org/',contract,task,result,[image],tmp_path/'out',requester=requester)
    assert report['numeric_checks'][0]['status']=='fail'
    assert report['status']=='inconclusive' and report['eligible_for_pipeline_gate'] is False


@pytest.mark.parametrize('intent',[
    'Confirm that no item was ever deleted.', 'Verify that nothing was modified throughout the workflow.',
    '确认整个过程从未删除笔记。', 'Read the page; do not create new records.',
    'Only view the page without sending any messages.', '查看列表，不得新建或编辑内容。',
])
def test_history_wide_claim_cannot_pass_from_screenshots_alone(tmp_path,intent):
    image=tmp_path/'screen.png';image.write_bytes(b'\x89PNG\r\n\x1a\n'+b'test')
    task={'task_id':'one','parameters':{},'intent':intent}
    result={'parameters':{},'result':{'answer':'yes'},'source_url':'https://example.org/'}
    response={'requirements':[{'requirement':'outcome','verdict':'pass','evidence':[
        {'image_id':'image_1','observation':'Two items visible'}]}]}
    requester=lambda *args: {'choices':[{'message':{'content':json.dumps(response)}}]}
    report=v.judge('https://example.org/',{'goal':intent,'returns':{'answer':'str'}},task,result,
                   [image],tmp_path/'out',requester=requester)
    assert report['status']=='inconclusive' and 'history_guard' in report


def test_omitted_requirement_is_a_known_model_judgment_limitation():
    # This single superficial requirement can pass even if a model omits
    # essential task requirements. We must not treat a pass as ground truth.
    value={'requirements':[{'requirement':'items are visible','verdict':'pass',
                           'evidence':[{'image_id':'image_1','observation':'items visible'}]}]}
    assert v.normalize_judgment(value,{'image_1'})['status']=='pass'


def test_replay_rejects_task_outside_development_split(tmp_path, monkeypatch):
    import replay_visual_development as replay
    root=tmp_path/'runs'; root.mkdir()
    run=root/'source'; (run/'tasks/development').mkdir(parents=True)
    (run/'summary.json').write_text('{}')
    (run/'tasks/development/tasks.json').write_text(json.dumps({'tasks':[{'task_id':'development_only'}]}))
    monkeypatch.setattr(replay,'RUN_ROOT',root)
    monkeypatch.setattr('sys.argv',['replay_visual_development.py',str(run),'not_development','run_001',
                                    '--output',str(root/'output')])
    with pytest.raises(ValueError,match='not in the development split'):
        replay.main()
    assert not (root/'output').exists()


def test_api_worker_wall_timeout_is_logged_and_no_key_in_command(tmp_path,monkeypatch):
    import subprocess
    ledger=[]
    monkeypatch.setattr(v,'_resolve_api_key',lambda *_:'private-test-key')
    monkeypatch.setattr(v,'_logger',lambda *_:None)
    monkeypatch.setattr(v,'_safe_detail_log',lambda *a,**k:None)
    monkeypatch.setattr(v,'_append_jsonl',lambda _,record:ledger.append(record))
    def run(command,**kwargs):
        assert 'private-test-key' not in ' '.join(command)
        assert json.loads(kwargs['input'])['key']=='private-test-key'
        assert kwargs['timeout']==10
        raise subprocess.TimeoutExpired(command,10)
    monkeypatch.setattr(v.subprocess,'run',run)
    payload={'model':'qwen3.5-plus','messages':[{'role':'user','content':[{'type':'text','text':'test'}]}]}
    with pytest.raises(TimeoutError,match='wall-clock'):
        v.model_request(payload,tmp_path,[],timeout_seconds=10)
    assert ledger[0]['status']=='api_failure' and ledger[0]['total_tokens'] is None


def test_real_worker_process_is_stopped_at_wall_deadline(tmp_path,monkeypatch):
    import time
    (tmp_path/'visual_api_worker.py').write_text('import time\ntime.sleep(10)\n')
    monkeypatch.setattr(v,'__file__',str(tmp_path/'visual_verifier.py'))
    monkeypatch.setattr(v,'_resolve_api_key',lambda *_:'private-test-key')
    monkeypatch.setattr(v,'_logger',lambda *_:None)
    monkeypatch.setattr(v,'_safe_detail_log',lambda *a,**k:None)
    monkeypatch.setattr(v,'_append_jsonl',lambda *a:None)
    payload={'model':'qwen3.5-plus','messages':[{'role':'user','content':[{'type':'text','text':'test'}]}]}
    start=time.monotonic()
    with pytest.raises(TimeoutError,match='wall-clock'):
        v.model_request(payload,tmp_path,[],timeout_seconds=1)
    assert time.monotonic()-start < 5


def test_worker_failure_kind_is_reported_without_sensitive_worker_output(tmp_path,monkeypatch):
    import subprocess
    ledger=[]
    monkeypatch.setattr(v,'_resolve_api_key',lambda *_:'private-test-key')
    monkeypatch.setattr(v,'_logger',lambda *_:None)
    monkeypatch.setattr(v,'_safe_detail_log',lambda *a,**k:None)
    monkeypatch.setattr(v,'_append_jsonl',lambda _,record:ledger.append(record))
    def run(*args,**kwargs):
        return subprocess.CompletedProcess(args[0],0,stdout=json.dumps(
            {'ok':False,'error_type':'RuntimeError','cause_type':'HTTPError',
             'http_status':429,'message':'private-test-key'}),stderr='')
    monkeypatch.setattr(v.subprocess,'run',run)
    payload={'model':'qwen3.5-plus','messages':[{'role':'user','content':[{'type':'text','text':'test'}]}]}
    with pytest.raises(v.WorkerRequestError) as error:
        v.model_request(payload,tmp_path,[],timeout_seconds=10)
    assert error.value.worker_error_type == 'RuntimeError'
    assert error.value.cause_type == 'HTTPError' and error.value.http_status == 429
    assert 'private-test-key' not in str(error.value)
    assert ledger[0]['worker_error_type']=='RuntimeError' and ledger[0]['http_status']==429
