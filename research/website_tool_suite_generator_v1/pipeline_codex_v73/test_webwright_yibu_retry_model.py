import asyncio
import httpx
import pytest

from webwright_yibu_model import YibuWebWrightModel
from webwright_yibu_retry_model import RetryYibuWebWrightModel


def http_error(status):
    request = httpx.Request('POST', 'https://example.test/')
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError('failed', request=request, response=response)


def test_two_500s_then_success(monkeypatch):
    calls = []
    async def fake(self, payload):
        calls.append(payload)
        if len(calls) < 3:
            raise http_error(500)
        return {'ok': True}
    async def no_wait(seconds):
        pass
    monkeypatch.setattr(YibuWebWrightModel, '_post_with_retries', fake)
    monkeypatch.setattr(asyncio, 'sleep', no_wait)
    model = RetryYibuWebWrightModel.__new__(RetryYibuWebWrightModel)
    assert asyncio.run(model._post_with_retries({'prompt': 'x'})) == {'ok': True}
    assert len(calls) == 3


@pytest.mark.parametrize(('status', 'expected_calls'), [(500, 3), (400, 1), (429, 1)])
def test_failure_stays_bounded(monkeypatch, status, expected_calls):
    calls = []
    async def fake(self, payload):
        calls.append(payload)
        raise http_error(status)
    async def no_wait(seconds):
        pass
    monkeypatch.setattr(YibuWebWrightModel, '_post_with_retries', fake)
    monkeypatch.setattr(asyncio, 'sleep', no_wait)
    model = RetryYibuWebWrightModel.__new__(RetryYibuWebWrightModel)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(model._post_with_retries({}))
    assert len(calls) == expected_calls
