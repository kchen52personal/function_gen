"""Private, no-model check that a saved session survives a fresh browser.

No account names, cookie values or page text are included in the result.
This diagnostic is not a benchmark trajectory or evidence of working playback.
"""
import asyncio
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse
from playwright.async_api import async_playwright


async def main():
    url, selector, cookie_name = sys.argv[1:]
    root = Path(os.environ['WORKSPACE_DIR'])
    os.umask(0o077)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=str(root/'private_storage_state.json'),
                                             viewport={'width':1280, 'height':1000})
        try:
            page = await context.new_page()
            await page.goto(url, wait_until='domcontentloaded', timeout=60000)
            await page.wait_for_timeout(3000)
            field = page.locator(selector)
            await field.fill('login-session-check', timeout=15000)
            await field.press('Enter', timeout=15000)
            await page.wait_for_timeout(5000)
            # Inspect only bounded authentication UI signals, not account content.
            login_prompt = False
            search_surface = False
            for frame in page.frames[:8]:
                try:
                    login_prompt |= await frame.get_by_text('扫码登录', exact=True).is_visible(timeout=2000)
                    search_surface |= 'search' in urlparse(frame.url).path
                except Exception:
                    pass
            cookies = await context.cookies(url)
            marker = any(c['name']==cookie_name and bool(c.get('value')) for c in cookies)
            result = {'status':'fresh_browser_checked', 'session_cookie_present':marker,
                      'qr_login_prompt_visible':login_prompt,
                      'search_surface_observed':search_surface,
                      'top_level_same_host':urlparse(page.url).hostname==urlparse(url).hostname,
                      'model_calls':0, 'gpu_used':False,
                      'playback_verified':False,
                      'limitations':['Cookie presence alone is not server authentication proof.',
                                     'Absence of a QR prompt does not establish song search or playback correctness.']}
            (root/'probe_result.json').write_text(json.dumps(result, indent=2)+'\n')
            print(json.dumps(result), flush=True)
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
