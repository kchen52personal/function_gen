import pytest
from score_saved_outputs import score, load_predictions


def test_strict_score_reports_missing_and_type_mismatch():
    gold=[{'task_id':'a','expected_result':{'value':1}},
          {'task_id':'b','expected_result':{'value':True}},
          {'task_id':'c','expected_result':{'value':'x'}}]
    predictions={'a':{'result':{'value':1}}, 'b':{'result':{'value':1}}}
    result=score(gold,predictions)
    assert (result['pass_count'],result['mismatch_count'],result['missing_count'])==(1,1,1)
    assert result['strict_pass_rate']==1/3


def test_duplicate_gold_ids_rejected():
    with pytest.raises(ValueError,match='unique'):
        score([{'task_id':'a','expected_result':0},{'task_id':'a','expected_result':1}],{})


def test_loads_result_from_saved_held_log(tmp_path):
    log=tmp_path/'case/workspace/execution/final_script_log.txt'
    log.parent.mkdir(parents=True)
    log.write_text('step 0 action: params:\nfinal result: {"result":{"title":"x"}}\n')
    result=score([{'task_id':'case','expected_result':{'title':'x'}}],load_predictions(tmp_path))
    assert result['pass_count']==1
