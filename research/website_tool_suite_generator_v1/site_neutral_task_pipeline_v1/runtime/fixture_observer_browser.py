"""Host-selected disposable fixture browser; never used on real accounts."""
import asyncio
import subprocess
import sys
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        process=subprocess.Popen([p.chromium.executable_path,'--headless','--no-sandbox',
            '--disable-dev-shm-usage','--disable-breakpad','--disable-crash-reporter',
            '--disable-crashpad','--no-first-run','--password-store=basic','--remote-debugging-port=19222',
            '--user-data-dir=/tmp/fixture-observer-profile','about:blank'],
            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            browser=None
            for _ in range(40):
                if process.poll() is not None:
                    detail=process.stderr.read().decode(errors='replace')[-1500:]
                    print('Chromium exited: '+detail,file=sys.stderr,flush=True)
                    raise RuntimeError('Chromium exited before CDP was ready')
                try:
                    browser=await p.chromium.connect_over_cdp('http://127.0.0.1:19222',timeout=1000)
                    break
                except Exception:
                    await asyncio.sleep(.25)
            if browser is None: raise RuntimeError('observer browser failed to start')
            page=browser.contexts[0].pages[0]
            await page.set_viewport_size({'width':1280,'height':1200})
            await page.goto('http://127.0.0.1:18765/',wait_until='networkidle',timeout=30000)
            await asyncio.sleep(220)
        finally:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill()


if __name__=='__main__':asyncio.run(main())
