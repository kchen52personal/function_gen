"""Inspect the public country-code chooser without entering personal data."""
import asyncio
import json
import os
from pathlib import Path
import sys
from playwright.async_api import async_playwright

async def main():
    root=Path(os.environ['WORKSPACE_DIR'])
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        page=await browser.new_page(viewport={'width':1280,'height':1000})
        await page.goto(sys.argv[1],wait_until='domcontentloaded',timeout=60000)
        await page.wait_for_timeout(5000)
        for frame in page.frames:
            try:
                target=frame.get_by_text('+86',exact=True)
                if await target.count():
                    await target.first.click(timeout=5000)
                    break
            except Exception:
                continue
        await page.wait_for_timeout(1000)
        await page.screenshot(path=str(root/'country_picker.png'))
        found=False
        await page.mouse.move(610,385)
        for _ in range(40):
            for frame in page.frames:
                try:
                    target=frame.get_by_text('加拿大',exact=False)
                    if await target.count() and await target.first.is_visible():
                        await target.first.click(timeout=3000)
                        found=True
                        break
                except Exception:
                    continue
            if found:
                break
            await page.mouse.wheel(0,400)
            await page.wait_for_timeout(150)
        await page.screenshot(path=str(root/'country_picker_scrolled.png'))
        await page.screenshot(path=str(root/'canada_selected.png'))
        (root/'canada_result.json').write_text(json.dumps({'canada_clicked':found})+'\n')
        texts=[]
        for frame in page.frames:
            try:
                texts.append((await frame.locator('body').text_content(timeout=3000))[:30000])
            except Exception:
                texts.append('unavailable')
        (root/'visible_text.json').write_text(json.dumps({'frames':texts},ensure_ascii=False))
        await browser.close()
asyncio.run(main())
