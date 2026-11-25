import pytest
from score_verifier_cases import score


def test_confusion_abstention_and_api_error_separate():
    rows=[{'case_id':'a','expected':'fail','actual':'pass','category':'x'},
          {'case_id':'b','expected':'pass','actual':'fail','category':'x'},
          {'case_id':'c','expected':'pass','actual':'inconclusive','category':'y'},
          {'case_id':'d','expected':'fail','actual':None,'execution':'api_error','category':'y'},
          {'case_id':'e','expected':'inconclusive','actual':'inconclusive','category':'y'}]
    result=score(rows)
    assert result['counts']=={'total':5,'judged':4,'false_pass':1,'false_fail':1,'abstention':1,'api_error':1,'expected_match':1}
    assert result['confusion']['fail']['pass']==1 and result['categories']['y']['api_error']==1


@pytest.mark.parametrize('rows',[
    [],[{'case_id':'a','expected':'pass','actual':'pass','category':'x'}]*2,
    [{'case_id':'a','expected':'pass','actual':'pass','category':'x','execution':'api_error'}],
    [{'case_id':'a','expected':'maybe','actual':'pass','category':'x'}]])
def test_invalid_records_fail(rows):
    with pytest.raises(ValueError):score(rows)
