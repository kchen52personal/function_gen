import json
from pathlib import Path

import pytest

import api_propose
from api_propose import _cost, _failed_usage, _model_used, _resolve_api_key, _response_text, _usage


def test_api_response_helpers_support_chat_usage() -> None:
    response = {
        "choices": [{"message": {"content": '{"candidates": []}'}}],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 4,
            "total_tokens": 16,
            "prompt_tokens_details": {"cached_tokens": 3},
            "completion_tokens_details": {"reasoning_tokens": 2},
        },
    }
    assert _response_text(response) == '{"candidates": []}'
    assert _usage(response) == {
        "input_tokens": 12,
        "cached_input_tokens": 3,
        "output_tokens": 4,
        "reasoning_output_tokens": 2,
        "total_tokens": 16,
    }
    assert _model_used({"model": "returned-model"}, "requested-model") == "returned-model"
    cost, source = _cost(response, _usage(response), 1.0, 2.0)
    assert cost == pytest.approx(0.00002)
    assert source == "configured_token_rates"


def test_api_response_read_is_bounded(monkeypatch) -> None:
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, size):
            assert size == api_propose.MAX_API_RESPONSE_BYTES + 1
            return b'x' * size
    monkeypatch.setattr(api_propose, 'urlopen', lambda *args, **kwargs: Response())
    with pytest.raises(ValueError, match='4 MiB limit'):
        api_propose._post_json('https://example.test/api', 'test-key', {}, 10)


def test_provider_reported_cost_takes_precedence() -> None:
    cost, source = _cost(
        {"usage": {"cost": 0.0123}},
        {"input_tokens": 100, "output_tokens": 20},
        99.0,
        99.0,
    )
    assert cost == 0.0123
    assert source == "provider_response.cost"


def test_monthly_quota_cost_is_unknown_not_zero() -> None:
    cost, source = _cost(
        {"usage": {}},
        {"input_tokens": 100, "output_tokens": 20},
        None,
        None,
    )
    assert cost is None
    assert source == "monthly_quota_rate_not_exposed"


def test_failed_request_usage_is_unknown_unless_reported() -> None:
    assert _failed_usage({}) == {'input_tokens': None, 'output_tokens': None,
        'total_tokens': None, 'cached_input_tokens': None, 'reasoning_output_tokens': None}
    partial = _failed_usage({'usage': {'prompt_tokens': 42}})
    assert partial['input_tokens'] == 42
    assert partial['output_tokens'] is None and partial['total_tokens'] is None
    complete = _failed_usage({'usage': {'prompt_tokens': 42, 'completion_tokens': 8}})
    assert complete['input_tokens'] == 42 and complete['output_tokens'] == 8
    assert complete['total_tokens'] == 50
    assert _failed_usage({'usage': {'prompt_tokens': True}})['input_tokens'] is None


def test_timeout_ledger_records_unknown_not_zero(tmp_path: Path, monkeypatch) -> None:
    recon = tmp_path / 'recon.json'
    recon.write_text(json.dumps({'start_url': 'https://example.test/',
                                 'allowed_host': 'example.test', 'pages': []}))
    ledger = tmp_path / 'ledger.jsonl'
    monkeypatch.setattr(api_propose, 'SPEND_LEDGER', ledger)
    monkeypatch.setattr(api_propose, '_resolve_api_key', lambda key: 'test-key')
    monkeypatch.setattr(api_propose, '_logger', lambda output: type('Logger', (), {
        'log_call': lambda self, **kwargs: None})())
    def timeout(*args, **kwargs):
        raise TimeoutError('unreported')
    monkeypatch.setattr(api_propose, '_post_json', timeout)
    with pytest.raises(TimeoutError):
        api_propose.propose_api(recon, tmp_path / 'proposal.json', endpoint='https://example.test/',
            model='test-model', key_file=None, max_output_tokens=512, max_input_chars=250000,
            input_usd_per_million=None, output_usd_per_million=None, max_usd=0.25,
            timeout_seconds=10, billing_mode='monthly_quota')
    entry = json.loads(ledger.read_text())
    assert entry['in'] is None and entry['out'] is None and entry['total_tokens'] is None
    assert entry['usd'] is None and entry['usage_source'] == 'incomplete_or_unreported_after_failure'
    assert entry['experiment_stage'] == 'candidate_proposal' and entry['experiment_artifact_path'] == str(tmp_path / 'proposal.json')


def test_oversized_response_logs_unknown_usage(tmp_path: Path, monkeypatch) -> None:
    recon = tmp_path / 'recon.json'
    recon.write_text(json.dumps({'start_url': 'https://example.test/',
                                 'allowed_host': 'example.test', 'pages': []}))
    ledger = tmp_path / 'ledger.jsonl'
    monkeypatch.setattr(api_propose, 'SPEND_LEDGER', ledger)
    monkeypatch.setattr(api_propose, '_resolve_api_key', lambda key: 'test-key')
    monkeypatch.setattr(api_propose, '_logger', lambda output: type('Logger', (), {
        'log_call': lambda self, **kwargs: None})())
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, size):
            return b'x' * size
    monkeypatch.setattr(api_propose, 'urlopen', lambda *args, **kwargs: Response())
    with pytest.raises(ValueError, match='4 MiB limit'):
        api_propose.propose_api(recon, tmp_path / 'proposal.json', endpoint='https://example.test/',
            model='test-model', key_file=None, max_output_tokens=512, max_input_chars=250000,
            input_usd_per_million=None, output_usd_per_million=None, max_usd=0.25,
            timeout_seconds=10, billing_mode='monthly_quota')
    entry = json.loads(ledger.read_text())
    assert entry['in'] is None and entry['out'] is None and entry['usd'] is None
    assert entry['status'] == 'rejected_api_or_parse'


def test_success_without_provider_usage_is_not_recorded_as_free(tmp_path: Path, monkeypatch) -> None:
    recon = tmp_path / 'recon.json'
    recon.write_text(json.dumps({'start_url': 'https://example.test/',
                                 'allowed_host': 'example.test', 'pages': []}))
    ledger = tmp_path / 'ledger.jsonl'
    monkeypatch.setattr(api_propose, 'SPEND_LEDGER', ledger)
    monkeypatch.setattr(api_propose, '_resolve_api_key', lambda key: 'test-key')
    monkeypatch.setattr(api_propose, '_logger', lambda output: type('Logger', (), {
        'log_call': lambda self, **kwargs: None})())
    monkeypatch.setattr(api_propose, '_post_json', lambda *args: {
        'choices': [{'message': {'content': '{"candidates": []}'}}]})
    result = api_propose.propose_api(recon, tmp_path / 'proposal.json', endpoint='https://example.test/',
        model='test-model', key_file=None, max_output_tokens=512, max_input_chars=250000,
        input_usd_per_million=1.0, output_usd_per_million=2.0, max_usd=1.0,
        timeout_seconds=10, billing_mode='token_rates')
    entry = json.loads(ledger.read_text())
    assert result['generation']['input_tokens'] is None
    assert entry['in'] is None and entry['out'] is None and entry['usd'] is None
    assert entry['usage_source'] == 'incomplete_or_unreported_after_success'
    assert entry['experiment_stage'] == 'candidate_proposal' and entry['experiment_artifact_path'] == str(tmp_path / 'proposal.json')


def test_detail_log_and_artifact_failure_do_not_hide_incurred_spend(tmp_path: Path, monkeypatch) -> None:
    recon = tmp_path / 'recon.json'
    recon.write_text(json.dumps({'start_url': 'https://example.test/',
                                 'allowed_host': 'example.test', 'pages': []}))
    ledger = tmp_path / 'ledger.jsonl'
    monkeypatch.setattr(api_propose, 'SPEND_LEDGER', ledger)
    monkeypatch.setattr(api_propose, '_resolve_api_key', lambda key: 'test-key')
    def broken_detail(*args, **kwargs):
        raise OSError('detail log unavailable')
    monkeypatch.setattr(api_propose, '_logger', lambda output: type('Logger', (), {
        'log_call': broken_detail})())
    monkeypatch.setattr(api_propose, '_post_json', lambda *args: {
        'choices': [{'message': {'content': '{"candidates": []}'}}],
        'usage': {'prompt_tokens': 7, 'completion_tokens': 2}})
    original_write = Path.write_text
    def fail_artifact(path, *args, **kwargs):
        if path.name == 'raw_api_output.txt':
            raise OSError('artifact unavailable')
        return original_write(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', fail_artifact)
    with pytest.raises(OSError, match='artifact unavailable'):
        api_propose.propose_api(recon, tmp_path / 'proposal.json', endpoint='https://example.test/',
            model='test-model', key_file=None, max_output_tokens=512, max_input_chars=250000,
            input_usd_per_million=None, output_usd_per_million=None, max_usd=0.25,
            timeout_seconds=10, billing_mode='monthly_quota')
    entry = json.loads(ledger.read_text())
    assert entry['in'] == 7 and entry['out'] == 2 and entry['usd'] is None
    assert entry['detail_log_error_type'] == 'OSError'


def test_failed_api_request_is_ledgered_even_when_detail_log_fails(tmp_path: Path, monkeypatch) -> None:
    recon = tmp_path / 'recon.json'
    recon.write_text(json.dumps({'start_url': 'https://example.test/',
                                 'allowed_host': 'example.test', 'pages': []}))
    ledger = tmp_path / 'ledger.jsonl'
    monkeypatch.setattr(api_propose, 'SPEND_LEDGER', ledger)
    monkeypatch.setattr(api_propose, '_resolve_api_key', lambda key: 'test-key')
    def broken(*args, **kwargs):
        raise OSError('detail failure')
    monkeypatch.setattr(api_propose, '_logger', lambda output: type('Logger', (), {'log_call': broken})())
    def timeout(*args, **kwargs):
        raise TimeoutError('provider timeout')
    monkeypatch.setattr(api_propose, '_post_json', timeout)
    with pytest.raises(TimeoutError):
        api_propose.propose_api(recon, tmp_path / 'proposal.json', endpoint='https://example.test/',
            model='test-model', key_file=None, max_output_tokens=512, max_input_chars=250000,
            input_usd_per_million=None, output_usd_per_million=None, max_usd=0.25,
            timeout_seconds=10, billing_mode='monthly_quota')
    entry = json.loads(ledger.read_text())
    assert entry['in'] is None and entry['usd'] is None
    assert entry['detail_log_error_type'] == 'OSError'


def test_api_key_file_must_be_private(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("WEBSITE_TOOL_API_KEY", raising=False)
    path = Path("/shared/hans") / tmp_path.name / "key"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("secret", encoding="utf-8")
    path.chmod(0o644)
    with pytest.raises(ValueError, match="group/others"):
        _resolve_api_key(path)


def test_api_key_can_come_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("WEBSITE_TOOL_API_KEY", "private-test-value")
    assert _resolve_api_key(None) == "private-test-value"
