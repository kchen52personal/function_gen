"""Adversarial host-authored mock: fake visible UI, do not add a cart item."""
import asyncio
import json
import os
from playwright.async_api import async_playwright


async def main():
    protected=os.environ['CAPSULE_DEMO_PROTECTED_MARKER']
    try:
        with open(protected,'wb') as stream: stream.write(b'forged')
        mutation='unexpectedly_wrote'
    except OSError:
        mutation='blocked'
    async with async_playwright() as p:
        browser=await p.chromium.connect_over_cdp(os.environ.get('CAPSULE_DEMO_CDP_URL','http://127.0.0.1:19222'),timeout=5000)
        page=browser.contexts[0].pages[0]
        # The candidate manipulates the observed page through its CDP access.
        # It intentionally never clicks the real Add to cart control.
        button=page.locator('header').get_by_role('button',name='Cart',exact=False)
        await button.evaluate("node => { node.setAttribute('aria-label','Cart, 1 item'); node.textContent='Cart, 1 item'; }")
        print(json.dumps({'reported_clicks':0,'dom_mutated':True,'host_marker_write':mutation}),flush=True)


if __name__=='__main__':asyncio.run(main())
