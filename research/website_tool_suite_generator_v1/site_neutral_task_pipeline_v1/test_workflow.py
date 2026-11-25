import hashlib
import json
from pathlib import Path
import pytest
import run as pipeline
import factory

def proposal():
    return {'contract': {'goal':'Find relevant public site records.', 'boundary':'Public read-only actions.',
        'success_contract':'Return visible values from the matching record.',
        'task_template':'Find the matching record for {query}.',
        'arguments':[{'name':'query','type':'string','description':'A search query'}],
        'returns':{'title':'str','link':'url'}},
        'cases':[{'query':f'uniqueitem{n:02d}'} for n in range(12)]}

@pytest.mark.parametrize(('url','idea'), [
    ('https://catalog.example/', 'Search a public catalog'),
    ('https://archive.example/', 'Find records in an archive'),
])
def test_no_manual_runtime_and_freeze_before_held(tmp_path, monkeypatch, url, idea):
    output = tmp_path/'run'
    async def recon(site, out, max_pages):
        return {'start_url':site,'allowed_host':site.split('/')[2], 'pages':[{'http_status':200,'frames':[]}]}
    monkeypatch.setattr(pipeline, 'reconnaissance', recon)
    monkeypatch.setattr(pipeline, 'create_taskset', lambda site, goal, rec, out:
        factory.create_taskset(site, goal, rec, out, requester=lambda *args:json.dumps(proposal())))
    def dev_run(site, contract, tasks, out, feedback=None, **kwargs):
        out.mkdir()
        records = []
        for task in tasks:
            source = out/task['task_id']/'qwen3.5-plus/agent/final_script.py'
            source.parent.mkdir(parents=True)
            source.write_text('print("mock script")')
            records.append({'task_id':task['task_id'],'trajectory_status':'Submitted','process_exit':0,
                            'script_sha256':pipeline.digest(source)})
        (out/'summary.json').write_text(json.dumps({'attempts':records}))
        return out
    monkeypatch.setattr(pipeline.development, 'run', dev_run)
    def eval_case(site, contract, task, script, out):
        if 'held_out' in out.parts:
            state = json.loads((output/'summary.json').read_text())
            assert state['held_opened'] is True and state['frozen_sha256'] == pipeline.digest(script)
        return {'task_id':task['task_id'], 'status':'model_agreement','reason':'mock-only'}
    monkeypatch.setattr(pipeline, 'evaluate_case', eval_case)
    def synth(site, contract, examples, out):
        dev_ids = {x['task_id'] for x in json.loads((output/'tasks/development/tasks.json').read_text())['tasks']}
        assert {x['task']['task_id'] for x in examples} <= dev_ids
        out.mkdir()
        script = out/'candidate.py'
        script.write_text('print("mock synthesized")')
        return {'status':'candidate_ready','candidate_sha256':pipeline.digest(script)}
    monkeypatch.setattr(pipeline, 'synthesize', synth)
    original = pipeline.digest
    def guard(path):
        if path == output/'tasks/held_out/tasks.json':
            state = json.loads((output/'summary.json').read_text())
            assert state['status'] == 'frozen_before_held' and state['held_opened'] is False
        return original(path)
    monkeypatch.setattr(pipeline, 'digest', guard)
    state = pipeline.execute(url, idea, output)
    assert state['manual_runtime_review'] is False
    assert state['status'] == 'complete_automated_assessment_only'
    assert state['held_model_agreements'] == 4

def test_refuses_before_held_when_development_fails(tmp_path, monkeypatch):
    output = tmp_path/'run'
    url = 'https://archive.example/'
    async def recon(site,out,max_pages):
        return {'start_url':site,'allowed_host':'archive.example','pages':[{'http_status':200,'frames':[]}]}
    monkeypatch.setattr(pipeline,'reconnaissance',recon)
    monkeypatch.setattr(pipeline,'create_taskset',lambda site,goal,rec,out:
        factory.create_taskset(site,goal,rec,out,requester=lambda *args:json.dumps(proposal())))
    def empty_dev(site, contract, tasks, out, **kwargs):
        out.mkdir()
        (out/'summary.json').write_text(json.dumps({'attempts':[]}))
        return out
    monkeypatch.setattr(pipeline.development,'run',empty_dev)
    state = pipeline.execute(url,'Find records in an archive',output)
    assert state['status'] == 'stopped_insufficient_development_evidence'
    assert state['held_opened'] is False
