"""Host-authored fixture mock: one add click followed by navigation click."""
import asyncio
import json
import os
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser=await p.chromium.connect_over_cdp(os.environ['CAPSULE_DEMO_CDP_URL'],timeout=5000)
        page=browser.contexts[0].pages[0]
        card=page.locator('.sf-card').filter(has=page.get_by_role('button',name='Add to cart',exact=True)).nth(1)
        await card.get_by_role('button',name='Add to cart',exact=True).click()
        await page.get_by_role('button',name='Northline',exact=True).click()
        await page.wait_for_timeout(500)
        print(json.dumps({'reported_clicks':2,'purpose':'two fixture interactions'}),flush=True)


if __name__=='__main__':asyncio.run(main())
