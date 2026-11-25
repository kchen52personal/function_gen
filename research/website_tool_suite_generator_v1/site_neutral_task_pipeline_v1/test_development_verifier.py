import json
import pytest
from development_verifier import verify_case


@pytest.mark.parametrize(('assessment','status','accepted'), [
    ({'status':'model_agreement','format_valid':True}, 'accepted', True),
    ({'status':'model_agreement','format_valid':False}, 'inconclusive', False),
    ({'status':'fail','reason':'output schema mismatch'}, 'rejected', False),
    ({'status':'inconclusive','reason':'source unavailable'}, 'inconclusive', False),
    ({'status':'unexpected'}, 'inconclusive', False),
])
def test_only_formatted_independent_agreement_is_accepted(tmp_path, assessment, status, accepted):
    script = tmp_path/'script.py'; script.write_text('# example')
    task = {'task_id':'dev','parameters':{'query':'development only'}}
    def evaluator(url, contract, seen_task, seen_script, output):
        assert seen_task == task
        assert 'held' not in json.dumps(seen_task)
        return assessment
    result = verify_case('https://example.org/', {'returns':{'title':'str'}}, task,
                         script, tmp_path/'verify', evaluator=evaluator)
    assert result['status'] == status
    assert result['accepted_for_synthesis'] is accepted
    assert result['is_ground_truth'] is False
    assert json.loads((tmp_path/'verify/verification.json').read_text())['status'] == status


def test_script_changed_during_check_is_rejected(tmp_path):
    script = tmp_path/'script.py'; script.write_text('# original')
    def evaluator(url, contract, task, path, output):
        path.write_text('# changed')
        return {'status':'model_agreement','format_valid':True}
    result = verify_case('https://example.org/', {}, {'task_id':'dev'}, script,
                         tmp_path/'verify', evaluator=evaluator)
    assert result['status'] == 'rejected' and not result['accepted_for_synthesis']


def test_provider_exception_is_inconclusive(tmp_path):
    script = tmp_path/'script.py'; script.write_text('# example')
    def evaluator(*args): raise TimeoutError('backend')
    result = verify_case('https://example.org/', {}, {'task_id':'dev'}, script,
                         tmp_path/'verify', evaluator=evaluator)
    assert result['status'] == 'inconclusive' and not result['accepted_for_synthesis']


def test_matching_wrong_answers_can_still_look_like_agreement():
    # This deliberately demonstrates a limitation: neither text grounding nor
    # model agreement establishes the answer's semantic relevance.
    from uniform_reference_agent import visible_reference_assessment
    contract = {'returns':{'title':'str'}}
    case = {'query':'the requested record'}
    same_wrong_output = {'parameters':case, 'result':{'title':'unrelated record'},
                         'source_url':'https://example.org/search'}
    grounding = {'cases':{'default':{'status':'values_found'}}}
    result = visible_reference_assessment('https://example.org/', contract, case,
                                          same_wrong_output, same_wrong_output, grounding)
    assert result['status'] == 'visible_reference_agreement'
    assert result['counts_as_verified'] is False
