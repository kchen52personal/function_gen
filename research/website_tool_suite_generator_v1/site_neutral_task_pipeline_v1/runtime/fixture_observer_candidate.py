"""Host-authored mock candidate, not WebWright or a real generated function."""
import asyncio
import json
import os
from playwright.async_api import async_playwright


async def main():
    # This host-only path is deliberately not mounted in this container.
    protected=os.environ['CAPSULE_DEMO_PROTECTED_MARKER']
    before=os.path.exists(protected)
    try:
        with open(protected,'wb') as stream: stream.write(b'forged')
        mutation='unexpectedly_wrote'
    except OSError:
        mutation='blocked'
    async with async_playwright() as p:
        browser=await p.chromium.connect_over_cdp(os.environ.get('CAPSULE_DEMO_CDP_URL','http://127.0.0.1:19222'),timeout=5000)
        page=browser.contexts[0].pages[0]
        card=page.locator('.sf-card').filter(has=page.get_by_role('button',name='Add to cart',exact=True)).nth(1)
        name=await card.locator('img').get_attribute('alt')
        await card.get_by_role('button',name='Add to cart',exact=True).click()
        await page.wait_for_timeout(600)
        print(json.dumps({'reported_clicks':1,'selected_product':name,
                          'host_marker_visible':before,'host_marker_write':mutation}),flush=True)


if __name__=='__main__':asyncio.run(main())
