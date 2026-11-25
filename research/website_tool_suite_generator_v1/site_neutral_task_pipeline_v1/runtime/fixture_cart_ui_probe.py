"""Host-only fixture QA, never a development trajectory or synthesis example."""
import asyncio
import argparse
import json
import os
from pathlib import Path
from playwright.async_api import async_playwright


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--modify-cart',action='store_true')
    args=parser.parse_args()
    root=Path(os.environ['WORKSPACE_DIR'])
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        page=await browser.new_page(viewport={'width':1440,'height':1600})
        await page.goto('http://127.0.0.1:18765/',wait_until='networkidle')
        names=[]
        for i in range(2):
            cards=page.locator('.sf-card').filter(has=page.get_by_role('button',name='Add to cart',exact=True))
            card=cards.nth(i)
            names.append(await card.locator('img').get_attribute('alt'))
            await card.get_by_role('button',name='Add to cart',exact=True).click()
            if await page.get_by_role('heading',name=names[-1],exact=True).count():
                await page.get_by_role('button',name='Add to cart',exact=True).first.click()
                close=page.get_by_role('button',name='Close cart',exact=True)
                if await close.is_visible():await close.click()
                await page.get_by_role('button',name='Northline',exact=True).click()
        await page.locator('header').get_by_role('button',name='Cart, 2 items',exact=True).click()
        await page.get_by_role('button',name='View full cart',exact=True).click()
        for name in names:await page.get_by_text(name,exact=True).first.wait_for()
        quantities=page.get_by_label('Quantity',exact=True)
        assert await quantities.count()==2, 'Two real cart lines required'
        await page.get_by_role('button',name='Increase quantity',exact=True).first.click()
        assert sorted(await quantities.all_text_contents())==['1','2']
        text_before=await page.locator('body').inner_text()
        modifications=None
        if args.modify_cart:
            await page.screenshot(path=str(root/'before_edit.png'),full_page=True)
            await page.get_by_role('button',name='Remove',exact=True).nth(1).click()
            assert await page.get_by_role('button',name=names[1],exact=True).count()==0
            await page.get_by_role('button',name='Increase quantity',exact=True).first.click()
            assert await quantities.all_text_contents()==['3']
            modifications={'removed':names[1],'updated':names[0],'new_quantity':3,
                           'body_after_edit':await page.locator('body').inner_text()}
            await page.screenshot(path=str(root/'after_edit.png'),full_page=True)
        # Publicly advertised demo code. This QA script is never a synthesis example.
        await page.get_by_placeholder('Promo code',exact=True).fill('WELCOME10')
        await page.get_by_role('button',name='Apply',exact=True).click()
        await page.get_by_text('WELCOME10 applied',exact=True).wait_for()
        text_after=await page.locator('body').inner_text()
        assert text_before!=text_after
        await page.screenshot(path=str(root/'after_add.png'),full_page=True)
        (root/'cart_probe.json').write_text(json.dumps({'qa_only':True,'not_pipeline_input':True,
            'clicked_product_names':names,'quantities':await quantities.all_text_contents(),
            'modifications':modifications,
            'body_before_promo':text_before,'body_after_add':text_after,
            'buttons':[await b.inner_text() for b in await page.get_by_role('button').all()]},indent=2)+'\n')
        await browser.close()


if __name__=='__main__':asyncio.run(main())
