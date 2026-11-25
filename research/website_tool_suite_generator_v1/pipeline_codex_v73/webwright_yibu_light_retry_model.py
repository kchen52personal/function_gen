"""Bounded gateway retries for a development-only WebWright experiment.

Each attempted POST goes through YibuWebWrightModel, which enforces the shared
call cap and records failed requests with unreported usage in the spend ledger.
A read timeout can already have incurred a provider charge.
"""
import asyncio
import httpx
from webwright_yibu_model import YibuWebWrightModel


class LightRetryYibuWebWrightModel(YibuWebWrightModel):
    async def _post_with_retries(self, payload):
        read_timeouts = 0
        for attempt in range(3):
            try:
                return await super()._post_with_retries(payload)
            except httpx.ReadTimeout:
                read_timeouts += 1
                if read_timeouts > 1 or attempt == 2:
                    raise
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {500, 502, 503, 504} or attempt == 2:
                    raise
            await asyncio.sleep(2 * (attempt + 1))
