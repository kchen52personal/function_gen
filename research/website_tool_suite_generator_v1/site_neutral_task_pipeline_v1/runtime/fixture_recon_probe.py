"""Trusted DOM reconnaissance for a host-selected isolated fixture; no task solution."""
import asyncio
import json
import os
from pathlib import Path
from playwright.async_api import async_playwright


async def main():
    root=Path(os.environ['WORKSPACE_DIR'])
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        page=await browser.new_page(viewport={'width':1280,'height':1800})
        response=await page.goto('http://127.0.0.1:18765/',wait_until='networkidle')
        await page.screenshot(path=str(root/'homepage.png'),full_page=True)
        frame=await page.evaluate('''() => ({url:location.href,
          headings:[...document.querySelectorAll('h1,h2,h3')].map(e=>({text:e.innerText})),
          links:[...document.querySelectorAll('a[href]')].map(e=>({text:e.innerText,href:e.href})),
          actions:[...document.querySelectorAll('button')].map(e=>({text:e.innerText,aria_label:e.getAttribute('aria-label')})),
          fields:[...document.querySelectorAll('input,select')].map(e=>({name:e.getAttribute('aria-label')||e.getAttribute('placeholder')||e.name,type:e.type})),
          content:document.body.innerText.split('\\n').filter(x=>x.trim()).slice(0,100).map(text=>({text}))})''')
        result={'start_url':page.url,'allowed_host':'127.0.0.1','pages':[{'requested_url':page.url,
                'final_url':page.url,'title':await page.title(),'http_status':response.status if response else None,
                'frames':[frame]}]}
        (root/'recon.json').write_text(json.dumps(result,indent=2)+'\n')
        await browser.close()


if __name__=='__main__': asyncio.run(main())
