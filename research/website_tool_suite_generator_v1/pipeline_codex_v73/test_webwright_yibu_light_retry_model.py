import asyncio
import httpx
import pytest

from webwright_yibu_model import YibuWebWrightModel
from webwright_yibu_light_retry_model import LightRetryYibuWebWrightModel


@pytest.mark.parametrize('failures, expected_calls', [
    ([httpx.ReadTimeout('slow')], 2),
    ([httpx.ReadTimeout('slow'), httpx.ReadTimeout('slow again')], 2),
])
def test_read_timeout_retries_once(monkeypatch, failures, expected_calls):
    calls = []
    async def fake(self, payload):
        calls.append(payload)
        if len(calls) <= len(failures):
            raise failures[len(calls) - 1]
        return {'ok': True}
    async def no_wait(seconds):
        pass
    monkeypatch.setattr(YibuWebWrightModel, '_post_with_retries', fake)
    monkeypatch.setattr(asyncio, 'sleep', no_wait)
    model = LightRetryYibuWebWrightModel.__new__(LightRetryYibuWebWrightModel)
    if len(failures) == 1:
        assert asyncio.run(model._post_with_retries({})) == {'ok': True}
    else:
        with pytest.raises(httpx.ReadTimeout):
            asyncio.run(model._post_with_retries({}))
    assert len(calls) == expected_calls


def test_non_transient_http_failure_not_retried(monkeypatch):
    calls = []
    request = httpx.Request('POST', 'https://example.test/')
    response = httpx.Response(400, request=request)
    async def fake(self, payload):
        calls.append(payload)
        raise httpx.HTTPStatusError('failed', request=request, response=response)
    monkeypatch.setattr(YibuWebWrightModel, '_post_with_retries', fake)
    model = LightRetryYibuWebWrightModel.__new__(LightRetryYibuWebWrightModel)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(model._post_with_retries({}))
    assert len(calls) == 1
