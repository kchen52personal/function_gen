import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from webwright_yibu_model import OpenRouterModel, YibuWebWrightModel, YibuWebWrightModelConfig


def test_explicit_higher_budget_is_bounded():
    assert YibuWebWrightModelConfig.validate_max_api_calls(110) == 110
    with pytest.raises(ValueError):
        YibuWebWrightModelConfig.validate_max_api_calls(111)
    with pytest.raises(ValueError):
        YibuWebWrightModelConfig.validate_max_api_calls(0)


def model_with_broken_detail_logger(tmp_path: Path) -> YibuWebWrightModel:
    model = YibuWebWrightModel.__new__(YibuWebWrightModel)
    model.config = SimpleNamespace(max_api_calls=2, model_name='test-model',
                                   api_log_path=tmp_path / 'api_call.json',
                                   spend_ledger_path=tmp_path / 'ledger.jsonl',
                                   openrouter_endpoint='https://example.test/api')
    model._api_call_count = 0
    class BrokenLogger:
        def log_call(self, **kwargs):
            raise OSError('detail log unavailable')
    model._api_logger = BrokenLogger()
    return model


def test_webwright_success_is_ledgered_when_detail_log_fails(tmp_path, monkeypatch) -> None:
    async def reply(self, payload):
        return {'model': 'actual-model', 'usage': {'prompt_tokens': 7, 'completion_tokens': 2}}
    monkeypatch.setattr(OpenRouterModel, '_post_with_retries', reply)
    model = model_with_broken_detail_logger(tmp_path)
    asyncio.run(model._post_with_retries({}))
    entry = json.loads((tmp_path / 'ledger.jsonl').read_text())
    assert entry['in'] == 7 and entry['out'] == 2 and entry['usd'] is None
    assert entry['detail_log_error_type'] == 'OSError'


def test_webwright_failure_is_ledgered_when_detail_log_fails(tmp_path, monkeypatch) -> None:
    async def fail(self, payload):
        raise TimeoutError('provider timeout')
    monkeypatch.setattr(OpenRouterModel, '_post_with_retries', fail)
    model = model_with_broken_detail_logger(tmp_path)
    with pytest.raises(TimeoutError):
        asyncio.run(model._post_with_retries({}))
    entry = json.loads((tmp_path / 'ledger.jsonl').read_text())
    assert entry['in'] is None and entry['out'] is None and entry['usd'] is None
    assert entry['detail_log_error_type'] == 'OSError'
