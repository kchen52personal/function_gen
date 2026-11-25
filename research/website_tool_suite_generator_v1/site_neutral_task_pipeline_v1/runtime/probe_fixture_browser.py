"""Run an actual Chromium interaction inside the test-only fixture sandbox."""
import json
from pathlib import Path
import subprocess

ROOT = Path('/shared/hans/research/website_tool_suite_generator_v1')
FIXTURE = ROOT / 'local_fixtures/ecommerce_storefront_20261117'
RUNTIME = Path(__file__).resolve().parent
OUT = ROOT / 'automated_runs/local_fixture_browser_probe_20261117_v1'
CHILD = r'''
import asyncio, json, os
from pathlib import Path
from urllib.parse import urlsplit
from playwright.async_api import async_playwright
async def main():
  blocked = []; images = []
  async with async_playwright() as playwright:
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context(service_workers='block')
    async def route(request):
      url = urlsplit(request.request.url)
      if url.scheme == 'http' and url.hostname == '127.0.0.1' and url.port == 18765:
        if url.path.startswith('/offline-image/'): images.append(url.path)
        await request.continue_()
      else:
        blocked.append(url.netloc); await request.abort()
    await context.route('**/*', route)
    page = await context.new_page()
    result = await page.goto('http://127.0.0.1:18765/', wait_until='networkidle', timeout=30000)
    await page.get_by_role('button',name='Shop all',exact=True).first.click()
    card = page.locator('.sf-card').filter(has_text='Aluminum Laptop Stand').first
    await card.wait_for(timeout=15000)
    text = await card.inner_text()
    await page.screenshot(path=str(Path(os.environ['WORKSPACE_DIR'])/'catalog.png'))
    print(json.dumps({'status':result.status,'price_visible':'$59.00' in text,
                      'images_served':len(images),'blocked':sorted(set(blocked))}))
    await browser.close()
asyncio.run(main())
'''


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    workspace = OUT / 'agent'
    workspace.mkdir()
    process = subprocess.run(['/shared/miniconda3/bin/python', str(RUNTIME/'sandbox_fixture_v1.py'),
        '/shared/miniconda3/bin/python', '-c', CHILD],
        env={'WORKSPACE_DIR': str(workspace), 'FIXTURE_ROOT': str(FIXTURE)},
        capture_output=True, text=True, timeout=60, check=False)
    try:
        result = json.loads(process.stdout)
    except ValueError:
        result = {'parse_error': True}
    result.update(returncode=process.returncode, stderr=process.stderr[-4000:])
    result['passed'] = (process.returncode == 0 and result.get('status') == 200
        and result.get('price_visible') is True and result.get('images_served', 0) > 0
        and not result.get('blocked') and (workspace/'catalog.png').is_file())
    (OUT/'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
