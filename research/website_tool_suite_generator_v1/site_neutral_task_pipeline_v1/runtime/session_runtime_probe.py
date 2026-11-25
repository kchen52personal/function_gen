"""Test automatic auth injection, with no explicit storage_state argument."""
import asyncio
import json
import os
from pathlib import Path
import sys
from playwright.async_api import async_playwright


async def main():
    url, cookie_name = sys.argv[1:]
    root=Path(os.environ['WORKSPACE_DIR'])
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        context=await browser.new_context()
        marker=any(c['name']==cookie_name and bool(c.get('value')) for c in await context.cookies(url))
        page=await context.new_page()
        await page.goto(url,wait_until='domcontentloaded',timeout=60000)
        await page.wait_for_timeout(1500)
        prompt=await page.get_by_text('扫码登录',exact=True).is_visible()
        await context.close()
        page2=await browser.new_page()
        page_marker=any(c['name']==cookie_name and bool(c.get('value')) for c in await page2.context.cookies(url))
        await page2.close()
        await browser.close()
    record={'context_session_injected':marker,'new_page_session_injected':page_marker,
            'qr_prompt_visible':prompt,'model_calls':0,'gpu_used':False,
            'private_state_in_workspace':any('storage_state' in p.name for p in root.iterdir())}
    (root/'probe_result.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record),flush=True)
    if not marker or not page_marker or prompt or record['private_state_in_workspace']:
        raise RuntimeError('authenticated runtime verification failed')


if __name__=='__main__':asyncio.run(main())
