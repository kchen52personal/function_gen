import json
import pytest
import coverage_verifier as c


def response(value):
    return {'model':'test','choices':[{'message':{'content':json.dumps(value)}}]}


def test_planner_has_no_answer_or_image_and_uses_stable_ids(tmp_path):
    def requester(payload,out):
        text=json.dumps(payload)
        assert 'secret_candidate_answer' not in text and 'image_url' not in text
        return response({'checks':[{'requirement':'Check title'},{'requirement':'Check date'}]})
    plan=c.plan_checks('https://example.org/',{'goal':'Name and date'},
                       {'task_id':'one','intent':'Name and date'},tmp_path/'plan',requester=requester)
    assert plan['status']=='ready' and [x['id'] for x in plan['checks']]==['R1','R2']
    assert plan['elapsed_seconds'] >= 0


def test_planner_worker_error_preserves_safe_diagnostics(tmp_path):
    def requester(*args):raise c.v.WorkerRequestError('RuntimeError','URLError',None)
    plan=c.plan_checks('https://example.org/',{}, {'task_id':'one'},tmp_path/'plan',requester=requester)
    assert plan['status']=='inconclusive' and plan['cause_type']=='URLError'
    assert plan['worker_error_type']=='RuntimeError' and plan['http_status'] is None


@pytest.mark.parametrize('checks',[[],[{'requirement':''}],[{'requirement':'same'},{'requirement':'same'}]])
def test_invalid_planner_output_stops_before_judging(tmp_path,checks):
    def planner(*args):return response({'checks':checks})
    def forbidden(*args):raise AssertionError('must not judge')
    report=c.assess('https://example.org/',{}, {'task_id':'one'}, {},[],tmp_path/'out',
                    planner_requester=planner,judge_requester=forbidden)
    assert report['status']=='inconclusive' and report['planner_status']=='inconclusive'


def test_omitted_checklist_item_cannot_become_pass(tmp_path):
    image=tmp_path/'screen.png';image.write_bytes(b'\x89PNG\r\n\x1a\n'+b'test')
    planner=lambda *args:response({'checks':[{'requirement':'Check title'},{'requirement':'Check date'}]})
    judge=lambda *args:response({'requirements':[{'id':'R1','requirement':'Check title','verdict':'pass',
        'evidence':[{'image_id':'image_1','observation':'Title visible'}]}]})
    task={'task_id':'one','parameters':{},'intent':'Title and date'}
    result={'parameters':{},'result':{'title':'x'},'source_url':'https://example.org/'}
    report=c.assess('https://example.org/',{'goal':'Title and date','returns':{'title':'str'}},task,result,
                    [image],tmp_path/'out',planner_requester=planner,judge_requester=judge)
    assert report['status']=='inconclusive' and report['judge']['coverage']['required_ids']==['R1','R2']


@pytest.mark.parametrize('fmt,expected',[('pass','pass'),('fail','fail'),(None,'inconclusive')])
def test_overall_status_includes_format(tmp_path,monkeypatch,fmt,expected):
    planner=lambda *args:response({'checks':[{'requirement':'Check value'}]})
    monkeypatch.setattr(c.v,'judge',lambda *a,**kw:{'status':'pass','format':{'status':fmt}})
    report=c.assess('https://example.org/',{}, {'task_id':'one'}, {},[],tmp_path/'out',
                    planner_requester=planner)
    assert report['status']==expected and report['semantic_status']=='pass'
