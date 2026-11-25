import json
import evaluate

def contract():
    return {'goal':'Find a public record','boundary':'Read-only', 'success_contract':'Return title',
            'arguments':[{'name':'query','type':'string','description':'Search query'}],
            'returns':{'title':'str'}}

def test_generic_assessment_uses_blind_reference_without_code_in_contract(tmp_path,monkeypatch):
    task = {'task_id':'case1','parameters':{'query':'water'},'intent':'Find water'}
    source = tmp_path/'input.py'
    source.write_text('print("example")')
    def fake_run(cmd, *, cwd, env, timeout, prefix):
        out = cwd/'execution'
        out.mkdir()
        data = {'parameters':task['parameters'],'result':{'title':'water'},'source_url':'https://example.org/search'}
        (out/'final_script_log.txt').write_text('step 0 action: params:\nfinal result: '+json.dumps(data))
        return 0
    async def ground(url, outputs):
        return {'cases':{'default':{'status':'values_found'}}}
    def reference(url, candidate, cases, outputs, report, root, env, calls, run_command, **kwargs):
        assert 'Find a public record' in candidate['goal']
        assert 'Specific request: Find water' in candidate['goal']
        assert 'script' not in candidate and 'result' not in candidate
        assert kwargs['visible_only'] is True
        assert kwargs['model_name'] == 'qwen3.8-flash'
        return {'status':'visible_reference_agreement'}
    monkeypatch.setattr(evaluate,'run_logged',fake_run)
    monkeypatch.setattr(evaluate,'ensure_screenshots',lambda *args:[])
    monkeypatch.setattr(evaluate,'grounding',ground)
    monkeypatch.setattr(evaluate,'blind_reference',reference)
    answer = evaluate.evaluate_case('https://example.org/',contract(),task,source,tmp_path/'case')
    assert answer['status'] == 'model_agreement'
    assert answer['independent_human_review'] is False

def test_nonzero_execution_fails_without_spending_on_reference(tmp_path,monkeypatch):
    source = tmp_path/'input.py'
    source.write_text('print("example")')
    monkeypatch.setattr(evaluate,'run_logged',lambda *args, **kwargs:1)
    task = {'task_id':'case1','parameters':{'query':'water'}}
    answer = evaluate.evaluate_case('https://example.org/',contract(),task,source,tmp_path/'case')
    assert answer['status'] == 'fail'

def test_timeout_is_inconclusive_not_a_semantic_failure(tmp_path,monkeypatch):
    source = tmp_path/'input.py'; source.write_text('print("example")')
    monkeypatch.setattr(evaluate,'run_logged',lambda *args, **kwargs:124)
    task = {'task_id':'case1','parameters':{'query':'water'}}
    answer = evaluate.evaluate_case('https://example.org/',contract(),task,source,tmp_path/'case')
    assert answer['status'] == 'inconclusive'

def test_raw_list_does_not_pass_or_get_silently_wrapped(tmp_path, monkeypatch):
    source = tmp_path/'input.py'
    source.write_text('print("example")')
    task = {'task_id':'case1', 'parameters':{'query':'water'}}
    shaped = {**contract(), 'returns':{'items':'list[object]'}}
    def fake_run(cmd, *, cwd, env, timeout, prefix):
        out = cwd/'execution'
        out.mkdir()
        result = {'parameters':task['parameters'], 'result':[{'title':'water'}],
                  'source_url':'https://example.org/search'}
        (out/'final_script_log.txt').write_text('step 0 action: params:\nfinal result: '+json.dumps(result))
        return 0
    monkeypatch.setattr(evaluate,'run_logged',fake_run)
    monkeypatch.setattr(evaluate,'ensure_screenshots',lambda *args:[])
    answer = evaluate.evaluate_case('https://example.org/',shaped,task,source,tmp_path/'case')
    assert answer['status'] == 'fail'
    assert 'fields do not exactly match' in answer['reason']

def test_inconclusive_reference_preserves_reason(tmp_path, monkeypatch):
    source = tmp_path/'input.py'
    source.write_text('print("example")')
    task = {'task_id':'case1','parameters':{'query':'water'}}
    def fake_run(cmd, *, cwd, env, timeout, prefix):
        out = cwd/'execution'
        out.mkdir()
        data = {'parameters':task['parameters'],'result':{'title':'water'},'source_url':'https://example.org/search'}
        (out/'final_script_log.txt').write_text('step 0 action: params:\nfinal result: '+json.dumps(data))
        return 0
    async def ground(*args): return {'cases':{'default':{'status':'values_found'}}}
    monkeypatch.setattr(evaluate,'run_logged',fake_run)
    monkeypatch.setattr(evaluate,'ensure_screenshots',lambda *args:[])
    monkeypatch.setattr(evaluate,'grounding',ground)
    monkeypatch.setattr(evaluate,'blind_reference',lambda *a, **kw: {'status':'reference_failed','reason':'reference exit 1'})
    answer = evaluate.evaluate_case('https://example.org/',contract(),task,source,tmp_path/'case')
    assert answer['status'] == 'inconclusive'
    assert answer['reference_reason'] == 'reference exit 1'
