import json
from batch_visual_verifier import assess


def fixture(tmp_path):
    evidence=[]
    for key,role in [('before','before'),('after','after'),('action0','action'),('action1','action')]:
        path=tmp_path/(key+'.png');path.write_bytes(b'\x89PNG\r\n\x1a\n'+key.encode())
        evidence.append({'key':key,'role':role,'path':path})
    checks=[{'id':'R1','requirement':'Check first action','evidence':['before','action0','after']},
            {'id':'R2','requirement':'Check second action','evidence':['action1','after']}]
    return evidence,checks


def requester(payload,out):
    instruction=payload['messages'][0]['content'][0]['text']
    checklist=json.loads(instruction.split('CHECKLIST: ',1)[1])
    rows=[]
    for check in checklist:
        rows.append({'id':check['id'],'requirement':check['requirement'],'verdict':'pass',
                     'evidence':[{'image_id':image,'observation':'Visible fixture evidence'} for image in check['images']]})
    return {'choices':[{'message':{'content':json.dumps({'requirements':rows})}}]}


def test_multi_batch_covers_each_required_image_without_gate(tmp_path):
    evidence,checks=fixture(tmp_path)
    task={'task_id':'one','parameters':{}}
    result={'parameters':{},'result':{'answer':'done'},'source_url':'https://example.org/'}
    report=assess('https://example.org/',{'returns':{'answer':'str'}},task,result,evidence,checks,tmp_path/'out',requester=requester)
    assert report['status']=='diagnostic_complete' and report['diagnostic_status']=='pass'
    assert len(report['batches'])==2 and not report['eligible_for_pipeline_gate']


def test_no_external_call_without_explicit_caller_authorization(tmp_path):
    evidence,checks=fixture(tmp_path)
    report=assess('https://example.org/',{}, {'task_id':'one'}, {},evidence,checks,tmp_path/'out')
    assert report['status']=='inconclusive' and 'not authorized' in report['reason']


def test_unreferenced_evidence_fails_before_judging(tmp_path):
    evidence,checks=fixture(tmp_path)
    def forbidden(*args):raise AssertionError('model should not run')
    report=assess('https://example.org/',{}, {'task_id':'one'}, {},evidence,checks[:1],tmp_path/'out',requester=forbidden)
    assert report['status']=='inconclusive' and report['batches']==[]


def test_metadata_is_labeled_per_batch_and_reserved_key_fails(tmp_path):
    evidence,checks=fixture(tmp_path);evidence[2]['metadata']={'mouse_x':42,'mouse_y':12}
    def verifying_requester(payload,out):
        text=payload['messages'][0]['content'][0]['text']
        assert 'observer_evidence' in text and 'mouse_x' in text and 'potentially incomplete' in text
        return requester(payload,out)
    task={'task_id':'one','parameters':{}}
    result={'parameters':{},'result':{'answer':'done'},'source_url':'https://example.org/'}
    # Only the first batch contains action0 and its metadata.
    seen=[]
    def first_only(payload,out):
        if not seen:verifying_requester(payload,out)
        seen.append(True);return requester(payload,out)
    report=assess('https://example.org/',{'returns':{'answer':'str'}},task,result,evidence,checks,
                  tmp_path/'out',requester=first_only)
    assert report['status']=='diagnostic_complete'
    report=assess('https://example.org/',{}, {**task,'observer_evidence':{}}, {},evidence,checks,
                  tmp_path/'out2',requester=requester)
    assert report['status']=='inconclusive' and 'reserved' in report['reason']


def test_missing_specific_image_cannot_become_batch_success(tmp_path):
    evidence,checks=fixture(tmp_path)
    def bad_requester(payload,out):
        response=requester(payload,out)
        value=json.loads(response['choices'][0]['message']['content'])
        value['requirements'][0]['evidence']=value['requirements'][0]['evidence'][-1:]
        response['choices'][0]['message']['content']=json.dumps(value)
        return response
    task={'task_id':'one','parameters':{}}
    result={'parameters':{},'result':{'answer':'done'},'source_url':'https://example.org/'}
    report=assess('https://example.org/',{'returns':{'answer':'str'}},task,result,evidence,checks,
                  tmp_path/'out',requester=bad_requester)
    assert report['status']=='inconclusive' and report['diagnostic_status'] is None
    assert not report['eligible_for_pipeline_gate']


def test_api_error_stops_remaining_batches(tmp_path):
    evidence,checks=fixture(tmp_path);calls=[]
    def failing(*args):calls.append(True);raise TimeoutError('simulated')
    task={'task_id':'one','parameters':{}}
    result={'parameters':{},'result':{'answer':'done'},'source_url':'https://example.org/'}
    report=assess('https://example.org/',{'returns':{'answer':'str'}},task,result,evidence,checks,
                  tmp_path/'out',requester=failing)
    assert report['status']=='inconclusive' and len(calls)==1 and len(report['batches'])==1
