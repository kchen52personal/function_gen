"""Browser/fixture probe run from the container-backed shell."""
import asyncio
import json
import os
from pathlib import Path
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        page=await browser.new_page()
        response=await page.goto('http://127.0.0.1:18765/',wait_until='networkidle',timeout=30000)
        await page.get_by_role('button',name='Shop all',exact=True).first.click()
        card=page.locator('.sf-card').filter(has_text='Aluminum Laptop Stand').first
        await card.wait_for(timeout=15000)
        text=await card.inner_text()
        await page.screenshot(path=str(Path(os.environ['WORKSPACE_DIR'])/'browser.png'))
        print(json.dumps({'status':response.status,'price_visible':'$59.00' in text}))
        await browser.close()


if __name__=='__main__': asyncio.run(main())
