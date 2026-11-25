"""Private, bounded login browser; never sends session contents to a model."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlparse
from playwright.async_api import async_playwright

async def main():
    url,x,y=sys.argv[1],float(sys.argv[2]),float(sys.argv[3])
    root=Path(os.environ['WORKSPACE_DIR']);os.umask(0o077)
    def status(name):
        (root/'status.json').write_text(json.dumps({'status':name,'updated_at_unix':time.time()})+'\n')
    status('opening')
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        context=await browser.new_context(viewport={'width':1280,'height':1000})
        page=await context.new_page()
        try:
            await page.goto(url,wait_until='domcontentloaded',timeout=60000)
            await page.wait_for_timeout(5000)
            await page.mouse.click(x,y)
            await page.wait_for_timeout(5000)
            # Third-party sign-in may open a popup rather than navigate the
            # original tab. Capture only the pre-authentication surface.
            login_page=context.pages[-1]
            await login_page.screenshot(path=str(root/'login.png'),timeout=15000)
            status('awaiting_user_scan_unverified')
            deadline=time.monotonic()+600
            while time.monotonic()<deadline:
                # The screenshot is captured only before sign-in. Persist private
                # state only after the browser returns to the expected app path.
                parsed=urlparse(page.url)
                if parsed.scheme=='https' and parsed.hostname=='note.youdao.com' and parsed.path.startswith('/web/'):
                    await context.storage_state(path=str(root/'private_storage_state.json'))
                    status('returned_to_app_requires_independent_verification')
                    return
                await asyncio.sleep(1)
            status('expired_without_confirmed_return')
        finally:
            await browser.close()

asyncio.run(main())
