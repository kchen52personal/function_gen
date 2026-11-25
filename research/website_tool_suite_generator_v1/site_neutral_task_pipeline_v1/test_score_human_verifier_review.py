import pytest
from score_human_verifier_review import compare


def row(case_id, human, prediction, criterion='requested_note_visible_on_final_screen'):
    return {'case_id': case_id, 'criterion': criterion, 'human_label': human, 'verifier_label': prediction}


def test_agreements_errors_and_abstentions_are_distinct():
    result = compare([row('1', 'yes', 'pass'), row('2', 'no', 'pass'),
                      row('3', 'yes', 'fail'), row('4', 'no', 'inconclusive'),
                      row('5', 'unsure', 'pass'), row('6', 'yes', None)])
    assert result['counts'] == {'agreement': 1, 'false_acceptance': 1, 'false_rejection': 1,
                                'verifier_inconclusive': 1, 'human_unsure': 1, 'prediction_missing': 1}


def test_does_not_use_screen_label_for_full_task():
    with pytest.raises(ValueError):
        compare([row('1', 'yes', 'pass', 'entire_task_succeeded')])


def test_rejects_duplicate_and_invalid_labels():
    with pytest.raises(ValueError):
        compare([row('1', 'yes', 'pass'), row('1', 'no', 'fail')])
    with pytest.raises(ValueError):
        compare([row('1', 'maybe', 'pass')])
