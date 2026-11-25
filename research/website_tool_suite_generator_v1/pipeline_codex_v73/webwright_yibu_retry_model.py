"""Bounded retry for transient gateway 5xx on agent requests only."""
import asyncio
import httpx
from webwright_yibu_model import YibuWebWrightModel


class RetryYibuWebWrightModel(YibuWebWrightModel):
    async def _post_with_retries(self, payload):
        # The superclass counts and logs every real request, including failed
        # retries, and enforces the configured overall call budget.
        for attempt in range(3):
            try:
                return await super()._post_with_retries(payload)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {500, 502, 503, 504} or attempt == 2:
                    raise
                await asyncio.sleep(2 * (attempt + 1))
