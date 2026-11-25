"""One UI-access diagnostic, excluded from development and function synthesis."""
import asyncio
import json
import os
from pathlib import Path
import sys
from playwright.async_api import async_playwright


async def main():
    url, selector, query=sys.argv[1:]
    root=Path(os.environ['WORKSPACE_DIR'])
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        page=await browser.new_page(viewport={'width':1280,'height':1000})
        await page.goto(url,wait_until='domcontentloaded',timeout=60000)
        await page.wait_for_timeout(1500)
        await page.screenshot(path=str(root/'before_search.png'))
        field=page.locator(selector)
        await field.fill(query,timeout=15000)
        await field.press('Enter',timeout=15000)
        await page.wait_for_timeout(5000)
        await page.screenshot(path=str(root/'after_search.png'))
        frames=[]
        for frame in page.frames[:8]:
            try:
                frames.append({'url':frame.url,'visible_text':(await frame.locator('body').inner_text(timeout=5000))[:16000]})
            except Exception as exc:
                frames.append({'url':frame.url,'error_type':type(exc).__name__})
        (root/'search_access.json').write_text(json.dumps({'query':query,'url':page.url,'frames':frames,
            'purpose':'Access diagnostic only; no candidate function, gold answers, login or playback.'},ensure_ascii=False,indent=2)+'\n')
        await browser.close()


if __name__=='__main__':asyncio.run(main())
