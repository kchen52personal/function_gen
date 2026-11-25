import json

import adaptive
import compare_qwen_multisite as comparison


def make_source(root,site):
    manifest=root/'tasks/development/tasks.json'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({'tasks':[{'task_id':f'task_{i}', 'parameters':{'query':f'case{i}'},
                                           'intent':f'Find case{i}'} for i in range(8)]}))
    (root/'tasks/contract.json').write_text(json.dumps({'returns':{'title':'str'}}))
    (root/'summary.json').write_text(json.dumps({'website':site,'split_hashes':{'development':adaptive.digest(manifest)}}))
    return root


def test_counterbalanced_multisite_run_never_reads_held_or_verifies(tmp_path,monkeypatch):
    source1=make_source(tmp_path/'one','https://one.example/')
    source2=make_source(tmp_path/'two','https://two.example/')
    calls=[]
    def run(site,contract,tasks,out,**kwargs):
        calls.append((site,tasks[0]['task_id'],kwargs['model'],kwargs['attempt_timeout']))
        script=out/tasks[0]['task_id']/kwargs['model']/'agent/final_script.py'
        script.parent.mkdir(parents=True);script.write_text('# source')
        (out/'summary.json').write_text(json.dumps({'attempts':[{'task_id':tasks[0]['task_id'],
            'trajectory_status':'Submitted','process_exit':0,'script_sha256':adaptive.digest(script)}]}))
    monkeypatch.setattr(comparison.development,'run',run)
    state=comparison.execute([source1,source2],tmp_path/'output')
    assert state['status']=='complete_submission_and_timing_only'
    assert len(calls)==8 and all(x[3]==1800 for x in calls)
    assert [x[2] for x in calls]==['qwen3.8-flash','qwen3.5-plus','qwen3.5-plus','qwen3.8-flash']*2
    assert all(calls[i][:2]==calls[i+1][:2] for i in range(0,8,2))
    assert all(x['correctness']=='not_assessed' for x in state['attempts'])
    assert state['held_out_opened'] is False and state['independent_correctness_verifier'] is False
