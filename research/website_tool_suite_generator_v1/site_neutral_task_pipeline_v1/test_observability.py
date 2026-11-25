"""Offline checks for metadata-only timing and relay failure reasons."""
import asyncio
import json
import socket
import threading
from pathlib import Path

import pytest
from cuawright.webwright.environments.local_workspace import LocalWorkspaceEnvironment
import webwright_safe_environment as safe
from webwright_yibu_model import OpenRouterModel
from test_webwright_spend_logging import model_with_broken_detail_logger
from runtime.public_egress import PublicEgress, error_reason
from runtime import tool_broker
from telemetry_summary import summarize


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_step_command_and_smoke_timing_has_no_raw_command_or_output(tmp_path, monkeypatch):
    timeline = tmp_path/'commands.jsonl'
    monkeypatch.setenv('CODEX_COMMAND_TIMELINE_PATH', str(timeline))
    env = safe.SafeToolWorkspaceEnvironment(output_dir=tmp_path, start_url='https://example.test/',
                                            shell='/sandbox', env={})
    monkeypatch.setattr(LocalWorkspaceEnvironment, 'execute', lambda self, action, cwd='': {
        'output':'private-output-marker', 'returncode':0,
        'observation':{'command_output':'private-output-marker'}})
    monkeypatch.setattr(env, '_smoke_changed_script', lambda: 'default: PASS: schema-valid')
    env.execute({'command':'echo private-command-marker'})
    events = rows(timeline)
    assert [e['event'] for e in events] == ['step_started','command_started','command_finished',
                                              'smoke_check_started','smoke_check_finished','step_finished']
    assert len({e['operation_id'] for e in events}) == 1
    assert events[2]['returncode'] == 0 and events[4]['status'] == 'passed_format_or_checkpoint'
    assert all(e.get('duration_seconds', 0) >= 0 for e in events)
    assert 'private-command-marker' not in timeline.read_text()
    assert 'private-output-marker' not in timeline.read_text()


def test_step_timeline_static_check_failure_is_not_a_pass(tmp_path, monkeypatch):
    timeline = tmp_path/'commands.jsonl'
    monkeypatch.setenv('CODEX_COMMAND_TIMELINE_PATH', str(timeline))
    env = safe.SafeToolWorkspaceEnvironment(output_dir=tmp_path, start_url='https://example.test/',
                                            shell='/sandbox', env={})
    monkeypatch.setattr(LocalWorkspaceEnvironment, 'execute', lambda self, action, cwd='': {
        'output':'', 'returncode':0, 'observation':{'command_output':''}})
    monkeypatch.setattr(env, '_smoke_changed_script', lambda: 'STATIC CHECK FAILED: SyntaxError')
    env.execute({'command':'inspect'})
    assert next(e for e in rows(timeline) if e['event']=='smoke_check_finished')['status'] == 'failed'


def test_main_model_start_finish_and_usage_recorded_without_prompt(tmp_path, monkeypatch):
    timeline = tmp_path/'models.jsonl'
    monkeypatch.setenv('CODEX_MODEL_TIMELINE_PATH', str(timeline))
    async def reply(self, payload):
        assert rows(timeline)[0]['event'] == 'request_started'
        return {'model':'returned-qwen', 'usage':{'prompt_tokens':7, 'completion_tokens':2}}
    monkeypatch.setattr(OpenRouterModel, '_post_with_retries', reply)
    model = model_with_broken_detail_logger(tmp_path)
    asyncio.run(model._post_with_retries({'prompt':'private-prompt-marker'}))
    start, end = rows(timeline)
    assert start['operation_id'] == end['operation_id']
    assert end['status'] == 'completed' and end['input_tokens'] == 7 and end['output_tokens'] == 2
    assert 'private-prompt-marker' not in timeline.read_text()


def test_main_model_failure_records_unknown_usage(tmp_path, monkeypatch):
    timeline = tmp_path/'models.jsonl'
    monkeypatch.setenv('CODEX_MODEL_TIMELINE_PATH', str(timeline))
    async def fail(self, payload):
        raise TimeoutError('private error detail')
    monkeypatch.setattr(OpenRouterModel, '_post_with_retries', fail)
    with pytest.raises(TimeoutError):
        asyncio.run(model_with_broken_detail_logger(tmp_path)._post_with_retries({}))
    start, end = rows(timeline)
    assert start['operation_id'] == end['operation_id'] and end['status'] == 'failed'
    assert end['error_type'] == 'TimeoutError' and end['usage'] == 'unreported'
    assert 'private error detail' not in timeline.read_text()


def test_interrupted_model_request_is_not_reported_as_zero_usage(tmp_path, monkeypatch):
    timeline = tmp_path/'models.jsonl'
    monkeypatch.setenv('CODEX_MODEL_TIMELINE_PATH', str(timeline))
    async def interrupted(self, payload):
        raise asyncio.CancelledError()
    monkeypatch.setattr(OpenRouterModel, '_post_with_retries', interrupted)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(model_with_broken_detail_logger(tmp_path)._post_with_retries({}))
    start, end = rows(timeline)
    assert start['operation_id'] == end['operation_id']
    assert end['status'] == 'interrupted' and end['usage'] == 'unreported'
    ledger = json.loads((tmp_path/'ledger.jsonl').read_text())
    assert ledger['status'] == 'interrupted' and ledger['in'] is None and ledger['out'] is None


def test_inner_model_records_start_finish_and_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_broker, '_logger', lambda _: None)
    monkeypatch.setattr(tool_broker, '_resolve_api_key', lambda _: 'mock-key')
    monkeypatch.setattr(tool_broker, '_post_json', lambda *args: {
        'model':'returned-qwen', 'usage':{'prompt_tokens':9, 'completion_tokens':3},
        'choices':[{'message':{'content':'stub'}}]})
    monkeypatch.setattr(tool_broker, '_safe_detail_log', lambda *args, **kwargs: None)
    monkeypatch.setattr(tool_broker, 'SPEND_LEDGER', tmp_path/'ledger.jsonl')
    with tool_broker.Broker(tmp_path, tmp_path, limit=1, pathname_socket=True) as broker:
        assert broker.call({'messages':[{'role':'user','content':'private-prompt-marker'}]}) == 'stub'
        with pytest.raises(RuntimeError):
            broker.call({'messages':[]})
    events = rows(tmp_path/'model_events.jsonl')
    assert [e['event'] for e in events] == ['request_started','request_finished','request_rejected']
    assert events[0]['operation_id'] == events[1]['operation_id']
    assert events[1]['input_tokens'] == 9 and events[1]['output_tokens'] == 3
    assert events[2]['reason'] == 'inner_call_budget_exhausted'
    assert 'private-prompt-marker' not in (tmp_path/'model_events.jsonl').read_text()


def test_relay_connection_budget_failure_has_explicit_reason(tmp_path):
    # Pre-fill the counter to test the actual denial path; no external network call.
    with PublicEgress(tmp_path, 'example.org', tmp_path/'network.jsonl') as egress:
        egress.count = 512
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(3)
            client.connect(str(tmp_path/'.egress.sock'))
            client.sendall(b'CONNECT example.org:443 HTTP/1.1\r\nHost: example.org\r\n\r\n')
            assert b'403 Forbidden' in client.recv(1024)
    start, event = rows(tmp_path/'network.jsonl')
    assert start['status'] == 'started' and start['connection_id'] == event['connection_id']
    assert event['reason'] == 'connection_budget_exhausted'
    assert event['phase'] == 'connection_budget'
    assert event['budget_count'] == 513 and event['budget_limit'] == 512
    assert event['started_utc'] and event['connection_id']


def test_relay_does_not_log_invalid_authority_or_raw_exception(tmp_path):
    relay = PublicEgress.__new__(PublicEgress)
    relay.lock = threading.Lock()
    relay.log = tmp_path/'network.jsonl'
    relay.record('user:private-marker@example.org:443/path', 'denied_or_failed', 'ValueError',
                 reason=error_reason(ValueError('untrusted private-marker'), 'parse'))
    assert rows(relay.log)[0]['authority'] == '<invalid>'
    assert 'private-marker' not in relay.log.read_text()


def test_timing_summary_handles_overlap_inflight_and_unknown_usage(tmp_path):
    def event(stage, name, operation, seconds, **kwargs):
        return {'stage':stage, 'event':name, 'operation_id':operation,
                'monotonic_ns':int(seconds*1e9), **kwargs}
    model = [
        event('webwright_model', 'request_started', 'main', 10),
        event('webwright_inner_model', 'request_started', 'inner', 20),
        event('webwright_model', 'request_finished', 'main', 30, status='completed',
              returned_model='qwen', input_tokens=10, output_tokens=2),
        event('webwright_inner_model', 'request_finished', 'inner', 40, status='failed',
              requested_model='qwen', error_type='TimeoutError'),
        event('webwright_model', 'request_started', 'interrupted', 45),
    ]
    (tmp_path/'model_events.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in model))
    command = [event('webwright_command','command_started','cmd',0),
               event('webwright_command','command_finished','cmd',50),
               event('webwright_command','smoke_check_started','smoke',30),
               event('webwright_command','smoke_check_finished','smoke',45,status='failed')]
    (tmp_path/'command_events.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in command))
    (tmp_path/'network.jsonl').write_text(json.dumps({'status':'denied_or_failed',
        'reason':'connection_budget_exhausted'})+'\n')
    data = summarize(tmp_path, 60)
    assert data['any_logged_model_active_union_seconds'] == 30
    assert data['timings']['main_model']['sum_seconds'] == 20
    assert data['timings']['inner_model']['sum_seconds'] == 20
    assert data['timings']['main_model']['started_without_finish'] == 1
    assert data['model_usage']['qwen']['usage_reported'] == 1
    assert data['model_usage']['qwen']['usage_unreported'] == 1
    assert data['smoke_outcomes'] == {'failed':1}
    assert data['network_failure_reasons'] == {'connection_budget_exhausted':1}


def test_timing_summary_marks_absent_files_unavailable(tmp_path):
    result = summarize(tmp_path, 12)
    assert result['available'] == {'command_events':False, 'model_events':False, 'network_events':False}
    assert result['timings']['main_model']['completed'] == 0
