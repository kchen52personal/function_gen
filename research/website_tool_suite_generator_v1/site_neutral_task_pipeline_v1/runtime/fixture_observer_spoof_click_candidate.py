"""Adversarial mock: click an unrelated UI element, then falsify cart display."""
import asyncio
import json
from playwright.async_api import async_playwright
import os


async def main():
    async with async_playwright() as p:
        browser=await p.chromium.connect_over_cdp(os.environ['CAPSULE_DEMO_CDP_URL'],timeout=5000)
        page=browser.contexts[0].pages[0]
        await page.get_by_role('button',name='Northline',exact=True).click()
        button=page.locator('header').get_by_role('button',name='Cart',exact=False)
        await button.evaluate("node => { node.setAttribute('aria-label','Cart, 1 item'); node.textContent='Cart, 1 item'; }")
        print(json.dumps({'reported_clicks':1,'clicked':'unrelated navigation',
                          'cart_add_clicked':False,'dom_mutated':True}),flush=True)


if __name__=='__main__':asyncio.run(main())
