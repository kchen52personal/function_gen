"""Inspect public login controls only; no credentials or authenticated state."""
import asyncio
import json
import os
from pathlib import Path
import sys
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        page=await browser.new_page(viewport={'width':1280,'height':1000})
        await page.goto(sys.argv[1],wait_until='domcontentloaded',timeout=60000)
        await page.wait_for_timeout(5000)
        result=[]
        for frame in page.frames:
            try:
                rows=await frame.evaluate('''() => Array.from(document.querySelectorAll('a,button,img,[role=button]')).map(e=>({tag:e.tagName,text:(e.innerText||'').slice(0,100),title:e.title,alt:e.alt,cls:String(e.className).slice(0,160),rect:(()=>{let r=e.getBoundingClientRect();return [r.x,r.y,r.width,r.height]})()})).filter(e=>e.rect[2]>0&&e.rect[3]>0).slice(0,200)''')
                result.append({'frame':frame.url.split('?')[0],'elements':rows})
            except Exception as e:
                result.append({'error':type(e).__name__})
        (Path(os.environ['WORKSPACE_DIR'])/'controls.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        await browser.close()
asyncio.run(main())
