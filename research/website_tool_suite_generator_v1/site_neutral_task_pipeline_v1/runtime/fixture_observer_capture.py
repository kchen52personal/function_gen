"""Capture bytes from host-selected browser over CDP; fixture prototype only."""
import asyncio
import base64
import json
import os
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser=await p.chromium.connect_over_cdp('http://127.0.0.1:19222',timeout=5000)
        if len(browser.contexts)!=1 or len(browser.contexts[0].pages)!=1:
            raise RuntimeError('unexpected observed browser structure')
        page=browser.contexts[0].pages[0]
        if not page.url.startswith('http://127.0.0.1:18765/'):
            raise RuntimeError('unexpected page')
        reloaded=os.environ.get('FIXTURE_OBSERVER_RELOAD')=='1'
        if reloaded:
            # Local-only diagnostic, never used for real accounts. A reload may
            # trigger side effects on other sites and is not a general verifier.
            await page.reload(wait_until='networkidle',timeout=30000)
        geometry=await page.evaluate('''() => ({scrollX:window.scrollX,scrollY:window.scrollY,
            viewportWidth:window.innerWidth,viewportHeight:window.innerHeight})''')
        cart=await page.locator('header').get_by_role('button',name='Cart',exact=False).get_attribute('aria-label')
        picture=await page.screenshot()
        print(json.dumps({'url':page.url,'title':await page.title(),'cart_label':cart,'reloaded':reloaded,'geometry':geometry,
                          'png':base64.b64encode(picture).decode('ascii')}),flush=True)


if __name__=='__main__':asyncio.run(main())
