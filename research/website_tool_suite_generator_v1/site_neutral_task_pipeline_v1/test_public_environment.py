"""No-model regression tests for the public container adapter and 8/4 boundary."""
import json
import os
import socket
from pathlib import Path
import pytest
import adaptive
import factory
from public_environment import IsolatedPublicEnvironment, snapshot
from runtime.public_egress import PublicEgress, public_addresses, target_host


@pytest.mark.parametrize('value', ['example.org:443', 'www.example.org:443'])
def test_same_site_https_allowed(value):
    assert target_host(value, 'example.org') == value[:-4]


@pytest.mark.parametrize('value', ['example.org:80', 'example.org:444', 'badexample.org:443',
    'example.org.attacker.test:443', '127.0.0.1:443', '[::1]:443', 'localhost:443',
    'user@example.org:443', 'example.org:443/path', 'example.org:443\r\nX:1'])
def test_other_destinations_denied(value):
    with pytest.raises(ValueError):
        target_host(value, 'example.org')


@pytest.mark.parametrize('address', ['127.0.0.1', '10.1.2.3', '192.168.0.1', '169.254.169.254',
    '100.64.0.1', '::1', 'fc00::1', '::ffff:127.0.0.1'])
def test_dns_private_reserved_denied(monkeypatch, address):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))])
    with pytest.raises(ValueError):
        public_addresses('example.org')


def test_mixed_public_private_dns_is_rejected(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('1.1.1.1', 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 443))])
    with pytest.raises(ValueError):
        public_addresses('example.org')


def test_artifacts_exclude_links_and_nonregular_files(tmp_path):
    source, dest = tmp_path/'src', tmp_path/'dst'
    source.mkdir()
    (source/'safe.txt').write_text('retained')
    (source/'unsafe').symlink_to('/shared/hans/AGENTS.md')
    os.mkfifo(source/'fifo')
    snapshot(source, dest)
    assert [p.name for p in dest.iterdir()] == ['safe.txt']


def test_public_environment_cannot_change_site(tmp_path):
    env = IsolatedPublicEnvironment(tmp_path, 'https://example.org/')
    with pytest.raises(ValueError):
        env.validate_site('https://other.test/', 'Search the public website')


def test_media_budget_changes_are_explicit_and_defaults_unchanged(tmp_path):
    normal=IsolatedPublicEnvironment(tmp_path,'https://media.example/')
    assert (normal.agent_steps,normal.agent_calls,normal.inner_calls)==(40,40,6)
    assert not normal.allow_media_playback and not normal.resource_hosts
    media=IsolatedPublicEnvironment(tmp_path,'https://media.example/',agent_steps=60,agent_calls=80,
        inner_calls=12,allow_media_playback=True,resource_hosts=('cdn.example',))
    assert (media.agent_steps,media.agent_calls,media.inner_calls)==(60,80,12)
    assert media.allow_media_playback and media.resource_hosts==('cdn.example',)
    with pytest.raises(ValueError):IsolatedPublicEnvironment(tmp_path,'https://media.example/',agent_steps=80,agent_calls=60)
    with pytest.raises(ValueError):IsolatedPublicEnvironment(tmp_path,'https://media.example/',resource_hosts=('127.0.0.1',))


def test_content_domain_allowance_does_not_open_unrelated_hosts():
    assert target_host('s2.cdn.example:443','media.example',('cdn.example',))=='s2.cdn.example'
    with pytest.raises(ValueError):target_host('unrelated.example:443','media.example',('cdn.example',))
    with pytest.raises(ValueError):target_host('cdn.example.attacker.test:443','media.example',('cdn.example',))


def test_media_synthesis_permission_is_consistent_and_opt_in():
    from synthesis import build_synthesis_prompt
    args=('https://media.example/',{'returns':{'status':'str'}},[{'script':'one'},{'script':'two'}])
    default,_=build_synthesis_prompt(*args)
    media,_=build_synthesis_prompt(*args,allow_media_playback=True)
    assert 'transient player state changes are allowed' not in default
    assert 'transient player state changes are allowed' in media
    assert 'read-only functionality; do not log in, change site state' not in media
    assert 'A button click alone' not in default
    assert 'a button click alone is not proof' in media


def test_public_adapter_uses_existing_pipeline_with_eight_sources_and_no_verifier(tmp_path, monkeypatch):
    from test_factory import proposal
    root = tmp_path/'run'
    class Environment(IsolatedPublicEnvironment):
        async def reconnaissance(self, url, out, max_pages):
            return {'start_url': url, 'allowed_host': self.host, 'pages': [{'http_status': 200}]}
        def create_taskset(self, url, idea, recon, out):
            return factory.create_taskset(url, idea, recon, out,
                requester=lambda *a: json.dumps(proposal('query', [f'item{x:02d}' for x in range(12)], idea)))
        def run_development(self, url, contract, tasks, out, **kwargs):
            out.mkdir(parents=True)
            records = []
            for task in tasks:
                script = out/task['task_id']/'qwen3.5-plus/agent/final_script.py'
                script.parent.mkdir(parents=True)
                script.write_text('# mock submitted script')
                records.append({'task_id': task['task_id'], 'trajectory_status': 'Submitted',
                                'process_exit': 0, 'script_sha256': adaptive.digest(script)})
            (out/'summary.json').write_text(json.dumps({'attempts': records}))
        def synthesize(self, url, contract, examples, out):
            assert len(examples) == 8
            assert not json.loads((root/'summary.json').read_text())['held_opened']
            out.mkdir(parents=True)
            (out/'candidate.py').write_text('# mock synthesized function')
            return {'status': 'candidate_ready', 'candidate_sha256': adaptive.digest(out/'candidate.py')}
        def run_ungraded_case(self, task, script, out):
            state = json.loads((root/'summary.json').read_text())
            assert state['held_opened'] and state['generation_closed']
            assert all(c['status'] == 'frozen_before_any_held' for c in state['checkpoints'])
            return {'task_id': task['task_id'], 'status': 'ran_ungraded', 'semantic_verdict': None}
    def forbidden(*args, **kwargs):
        raise AssertionError('Independent verifier must not affect this run')
    monkeypatch.setattr(adaptive, 'verify_case', forbidden)
    monkeypatch.setattr(adaptive, 'evaluate_case', forbidden)
    state = adaptive.execute('https://example.org/', 'Search public records', root,
        environment=Environment(root, 'https://example.org/'), final_only=True, webwright_only=True)
    assert state['submitted_development_count'] == 8
    assert len(state['checkpoints']) == 1
    assert len(state['checkpoints'][0]['held_results']) == 4
    assert not any('fixed local demo' in text for text in state['limitations'])
    assert any('TLS is not intercepted' in text for text in state['limitations'])


def test_fifty_held_rows_only_run_after_eight_source_freeze(tmp_path,monkeypatch):
    from test_factory import proposal
    root=tmp_path/'run'
    class Environment(IsolatedPublicEnvironment):
        async def reconnaissance(self,url,out,max_pages):
            return {'start_url':url,'allowed_host':self.host,'pages':[{'http_status':200}]}
        def create_taskset(self,url,idea,recon,out,held_count=4):
            assert held_count==50
            return factory.create_taskset(url,idea,recon,out,held_count=held_count,
                requester=lambda *a:json.dumps(proposal('query',[f'song{x:03d}' for x in range(58)],idea)))
        def run_development(self,url,contract,tasks,out,**kwargs):
            out.mkdir(parents=True)
            rows=[]
            for task in tasks:
                script=out/task['task_id']/'qwen3.5-plus/agent/final_script.py'
                script.parent.mkdir(parents=True);script.write_text('# mock')
                rows.append({'task_id':task['task_id'],'trajectory_status':'Submitted','process_exit':0,
                             'script_sha256':adaptive.digest(script)})
            (out/'summary.json').write_text(json.dumps({'attempts':rows}))
        def synthesize(self,url,contract,examples,out):
            assert len(examples)==8
            assert not json.loads((root/'summary.json').read_text())['held_opened']
            dev=json.loads((root/'tasks/development/tasks.json').read_text())['tasks']
            assert {x['task']['task_id'] for x in examples}=={x['task_id'] for x in dev}
            out.mkdir(parents=True);(out/'candidate.py').write_text('# mock frozen')
            return {'status':'candidate_ready','candidate_sha256':adaptive.digest(out/'candidate.py')}
        def run_ungraded_case(self,task,script,out):
            state=json.loads((root/'summary.json').read_text())
            assert state['generation_closed'] and state['held_opened']
            return {'task_id':task['task_id'],'status':'ran_ungraded','semantic_verdict':None}
    state=adaptive.execute('https://media.example/','Search and play a song',root,
        environment=Environment(root,'https://media.example/',agent_steps=60,agent_calls=80,inner_calls=12,
                               allow_media_playback=True),held_count=50,final_only=True,webwright_only=True)
    assert state['limits']['held_out']==50
    assert state['limits']['agent_steps']==60 and state['limits']['agent_calls']==80
    assert len(state['checkpoints'][0]['held_results'])==50
    assert state['submitted_development_count']==8 and state['media_playback_allowed']


def test_sixteen_sources_freeze_before_fifty_held_runs(tmp_path):
    from test_factory import proposal
    root=tmp_path/'run'
    class Environment(IsolatedPublicEnvironment):
        async def reconnaissance(self,url,out,max_pages):
            return {'start_url':url,'allowed_host':self.host,'pages':[{'http_status':200}]}
        def create_taskset(self,url,idea,recon,out,held_count=4,development_count=8):
            assert (development_count,held_count)==(16,50)
            return factory.create_taskset(url,idea,recon,out,held_count=held_count,
                development_count=development_count,
                requester=lambda *a:json.dumps(proposal('request',[f'Play unique track {x:03d}' for x in range(66)],idea)))
        def run_development(self,url,contract,tasks,out,**kwargs):
            assert len(tasks)==16
            out.mkdir(parents=True);records=[]
            for task in tasks:
                script=out/task['task_id']/'qwen3.5-plus/agent/final_script.py'
                script.parent.mkdir(parents=True);script.write_text('# mock submitted')
                records.append({'task_id':task['task_id'],'trajectory_status':'Submitted',
                                'process_exit':0,'script_sha256':adaptive.digest(script)})
            (out/'summary.json').write_text(json.dumps({'attempts':records}))
        def synthesize(self,url,contract,examples,out):
            assert len(examples)==16
            assert not json.loads((root/'summary.json').read_text())['held_opened']
            out.mkdir(parents=True);(out/'candidate.py').write_text('# mock frozen')
            return {'status':'candidate_ready','candidate_sha256':adaptive.digest(out/'candidate.py')}
        def run_ungraded_case(self,task,script,out):
            state=json.loads((root/'summary.json').read_text())
            assert state['generation_closed'] and state['held_opened']
            assert state['submitted_development_count']==16
            return {'task_id':task['task_id'],'status':'ran_ungraded','semantic_verdict':None}
    state=adaptive.execute('https://example.org/','Play media',root,target=16,development_count=16,
        held_count=50,final_only=True,webwright_only=True,environment=Environment(root,'https://example.org/'))
    assert len(state['checkpoints'])==1
    assert state['checkpoints'][0]['development_submission_count']==16
    assert len(state['checkpoints'][0]['held_results'])==50
