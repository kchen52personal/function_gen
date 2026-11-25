"""Fixture-only mock: click search, then type and submit a query."""
import asyncio
import json
import os
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser=await p.chromium.connect_over_cdp(os.environ['CAPSULE_DEMO_CDP_URL'],timeout=5000)
        page=browser.contexts[0].pages[0]
        field=page.get_by_placeholder('Search',exact=True)
        await field.click();await field.fill('Desk Mat');await field.press('Enter')
        await page.wait_for_timeout(600)
        print(json.dumps({'interaction':'mouse_and_keyboard_search'}),flush=True)


if __name__=='__main__':asyncio.run(main())
