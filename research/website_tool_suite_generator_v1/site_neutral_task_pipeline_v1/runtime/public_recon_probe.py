"""Trusted one-page DOM reconnaissance; no task examples, solutions or selectors."""
import asyncio
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse
from playwright.async_api import async_playwright


async def main():
    url = sys.argv[1]
    root = Path(os.environ['WORKSPACE_DIR'])
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width': 1280, 'height': 1000})
        response = await page.goto(url, wait_until='domcontentloaded', timeout=60000)
        await page.wait_for_timeout(1500)
        await page.screenshot(path=str(root/'homepage.png'))
        frames = []
        for frame in page.frames[:8]:
            try:
                record = await frame.evaluate('''() => ({url:location.href,
          headings:[...document.querySelectorAll('h1,h2,h3')].slice(0,50).map(e=>({text:e.innerText})),
          links:[...document.querySelectorAll('a[href]')].slice(0,120).map(e=>({text:e.innerText,href:e.href})),
          actions:[...document.querySelectorAll('button')].slice(0,50).map(e=>({text:e.innerText,aria_label:e.getAttribute('aria-label')})),
          fields:[...document.querySelectorAll('input,select')].slice(0,50).map(e=>({name:e.getAttribute('aria-label')||e.getAttribute('placeholder')||e.name,type:e.type})),
          forms:[...document.querySelectorAll('form')].slice(0,10).map(e=>({text:e.innerText,href:e.action,name:e.name})),
          content:(document.body?.innerText||'').split('\\n').filter(x=>x.trim()).slice(0,100).map(text=>({text}))})''')
                frames.append(record)
            except Exception as exc:
                frames.append({'url': frame.url, 'probe_error': type(exc).__name__})
        result = {'start_url': url, 'allowed_host': urlparse(url).hostname,
                  'pages': [{'requested_url': url, 'final_url': page.url,
                             'title': await page.title(), 'http_status': response.status if response else None,
                             'frames': frames}], 'max_pages': 1}
        (root/'recon.json').write_text(json.dumps(result, indent=2)+'\n')
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
