"""Interactive login only: no agent, model, benchmark task or candidate code.

The caller supplies UI selectors observed during access diagnosis. Private state
stays in a mode-0700 workspace and is never copied by the artifact snapshotter.
"""
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlparse


def session_cookie_present(cookies, name, host):
    return any(c.get('name') == name and bool(c.get('value')) and
               (host == c.get('domain', '').lstrip('.') or
                host.endswith('.'+c.get('domain', '').lstrip('.')))
               for c in cookies if c.get('domain'))


async def main():
    from playwright.async_api import async_playwright
    url, selector, cookie_name = sys.argv[1:]
    host = urlparse(url).hostname
    root = Path(os.environ['WORKSPACE_DIR'])
    os.umask(0o077)

    def status(state):
        temporary = root/'status.tmp'
        temporary.write_text(json.dumps({'status': state, 'updated_at_unix': time.time(),
                                         'model_calls': 0, 'gpu_used': False})+'\n')
        temporary.replace(root/'status.json')

    status('opening_login_browser')
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width':1280, 'height':1000})
        page = await context.new_page()
        crashed = False
        def on_crash():
            nonlocal crashed
            crashed = True
            status('renderer_crashed_session_not_confirmed')
        page.on('crash', on_crash)
        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=60000)
            await page.wait_for_timeout(1500)
            field = page.locator(selector)
            await field.fill('login-session-bootstrap', timeout=15000)
            await field.press('Enter', timeout=15000)
            await page.wait_for_timeout(5000)
            deadline = time.monotonic()+660
            have_image = False
            last_save = 0
            while time.monotonic() < deadline:
                # Values are never written to status, stdout, or public artifacts.
                cookies = await context.cookies()
                received = session_cookie_present(cookies, cookie_name, host)
                if received or time.monotonic()-last_save >= 5:
                    state = root/'private_storage_state.tmp.json'
                    await context.storage_state(path=str(state))
                    state.replace(root/'private_storage_state.json')
                    last_save = time.monotonic()
                if received:
                    status('session_cookie_received_unverified')
                    return
                if crashed:
                    raise RuntimeError('login renderer crashed before session confirmation')
                # Screenshot failure must not kill the login/session checkpoint loop.
                # Avoid repeatedly rendering a large page while a user scans.
                refresh = root/'refresh_image_requested'
                if not have_image or refresh.exists():
                    try:
                        temporary = root/'login.tmp.png'
                        await page.screenshot(path=str(temporary), timeout=10000)
                        temporary.replace(root/'login.png')
                        have_image = True
                        if refresh.exists():
                            refresh.unlink()
                    except Exception:
                        if not browser.is_connected():
                            raise
                        status('screenshot_failed_still_monitoring_session')
                        await asyncio.sleep(1)
                        continue
                status('waiting_for_user_scan')
                await asyncio.sleep(1)
            status('expired_without_session')
        except BaseException:
            status('login_bootstrap_failed')
            raise
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
