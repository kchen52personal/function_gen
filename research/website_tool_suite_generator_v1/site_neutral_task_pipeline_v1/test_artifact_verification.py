import json
from pathlib import Path

from artifact_verification import verify_saved


HASH = 'a'*64
TASK = {'task_id':'case', 'parameters':{}}


def artifacts(root):
    root.mkdir()
    (root/'screenshots').mkdir()
    (root/'screenshots/after.png').write_bytes(b'fake image for stub judge')
    (root/'final_script_log.txt').write_text('final result: '+json.dumps({'result':{}}))


def stub(status, fmt='pass'):
    def judge(*args, **kwargs):
        return {'status':status, 'format':{'status':fmt}}
    return judge


def test_development_visual_pass_is_not_independent_acceptance(tmp_path):
    root=tmp_path/'execution'; artifacts(root)
    result=verify_saved('https://example.org',{},TASK,root,tmp_path/'out',stage='development',
                        script_sha256=HASH,evidence_script_sha256=HASH,judge=stub('pass'))
    assert result['status']=='inconclusive' and result['diagnostic_status']=='pass' and not result['accepted_for_synthesis']
    assert not result['eligible_for_pipeline_gate']
    assert not result['is_ground_truth']


def test_missing_or_mismatched_provenance_fails_closed(tmp_path):
    root=tmp_path/'execution'; artifacts(root)
    for index, binding in enumerate((None,'b'*64)):
        result=verify_saved('https://example.org',{},TASK,root,tmp_path/str(index),stage='development',
                            script_sha256=HASH,evidence_script_sha256=binding,judge=stub('pass'))
        assert result['status']=='inconclusive' and not result['accepted_for_synthesis']


def test_missing_after_screenshot_is_inconclusive(tmp_path):
    root=tmp_path/'execution'; artifacts(root)
    (root/'screenshots/after.png').unlink()
    result=verify_saved('https://example.org',{},TASK,root,tmp_path/'out',stage='held_out',
                        script_sha256=HASH,evidence_script_sha256=HASH,judge=stub('pass'))
    assert result['status']=='inconclusive'


def test_failure_and_uncertainty_do_not_accept(tmp_path):
    root=tmp_path/'execution'; artifacts(root)
    for index, (semantic, fmt, expected) in enumerate((('fail','pass','fail'),
            ('pass','fail','fail'),('inconclusive','pass','inconclusive'))):
        result=verify_saved('https://example.org',{},TASK,root,tmp_path/str(index),stage='development',
                            script_sha256=HASH,evidence_script_sha256=HASH,judge=stub(semantic,fmt))
        assert result['status']=='inconclusive' and result['diagnostic_status']==expected and not result['accepted_for_synthesis']


def test_held_result_never_accepted_as_development(tmp_path):
    root=tmp_path/'execution'; artifacts(root)
    result=verify_saved('https://example.org',{},TASK,root,tmp_path/'out',stage='held_out',
                        script_sha256=HASH,evidence_script_sha256=HASH,judge=stub('pass'))
    assert result['status']=='inconclusive' and result['diagnostic_status']=='pass' and not result['accepted_for_synthesis']
