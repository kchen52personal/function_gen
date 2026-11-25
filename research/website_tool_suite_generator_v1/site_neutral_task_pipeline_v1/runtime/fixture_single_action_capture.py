"""Trusted fixture QA, not a generated candidate or production observer.

Capture a disposable local store before and after exactly one button click in
one browser session. A successful run does not validate arbitrary scripts.
"""
import asyncio
import json
import os
from pathlib import Path
from playwright.async_api import async_playwright


async def main():
    root=Path(os.environ['WORKSPACE_DIR'])
    report={'fixture_only':True,'candidate_executed':False,'scripted_clicks':0,
            'purpose':'one-session before/after capture plumbing check',
            'limitations':['Screenshots are written in the test workspace.',
                           'No adversarial candidate or protected host observer is running.']}
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        try:
            page=await browser.new_page(viewport={'width':1280,'height':1200})
            await page.goto('http://127.0.0.1:18765/',wait_until='networkidle',timeout=30000)
            card=page.locator('.sf-card').filter(has=page.get_by_role('button',name='Add to cart',exact=True)).nth(1)
            report['selected_product']=await card.locator('img').get_attribute('alt')
            await card.scroll_into_view_if_needed()
            await page.screenshot(path=str(root/'before.png'))
            await card.get_by_role('button',name='Add to cart',exact=True).click(timeout=15000)
            report['scripted_clicks']+=1
            await page.wait_for_timeout(700)
            report['visible_cart_label']=await page.locator('header').get_by_role('button',name='Cart',exact=False).get_attribute('aria-label')
            await page.screenshot(path=str(root/'after.png'))
            report['status']='captured'
        finally:
            await browser.close()
            (root/'capture_report.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':asyncio.run(main())
