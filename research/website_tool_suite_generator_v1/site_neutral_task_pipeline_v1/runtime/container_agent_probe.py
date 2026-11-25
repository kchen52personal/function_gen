"""Runs inside an isolated container. No model, credentials or live websites."""
import asyncio
import json
from pathlib import Path
import socket
import sys
from urllib.parse import urlsplit
from playwright.async_api import async_playwright


def socket_result(family, address):
    try:
        with socket.socket(family, socket.SOCK_STREAM) as conn:
            conn.settimeout(2)
            conn.connect(address)
            if family == socket.AF_UNIX:
                conn.sendall(b'probe')
                return 'connected:' + conn.recv(32).decode()
            return 'connected'
    except OSError as exc:
        return type(exc).__name__


async def browse():
    blocked = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(service_workers='block')
        async def route_request(route):
            url = urlsplit(route.request.url)
            if url.scheme == 'http' and url.hostname == '127.0.0.1' and url.port == 18765:
                await route.continue_()
            else:
                blocked.append(url.netloc or url.scheme)
                await route.abort()
        await context.route('**/*',route_request)
        page=await context.new_page()
        response=await page.goto('http://127.0.0.1:18765/',wait_until='networkidle',timeout=30000)
        await page.get_by_role('button',name='Shop all',exact=True).first.click()
        item=page.locator('.sf-card').filter(has_text='Aluminum Laptop Stand').first
        await item.wait_for(timeout=15000)
        text=await item.inner_text()
        await page.screenshot(path='/agent/catalog.png')
        broken=await page.locator('img').evaluate_all('imgs => imgs.filter(i => !i.complete || !i.naturalWidth).length')
        await browser.close()
    return {'status':response.status,'price_visible':'$59.00' in text,'broken_images':broken,
            'nonlocal_requests':sorted(set(blocked))}


def main():
    value={'fake_broker':socket_result(socket.AF_UNIX,'/agent/broker.sock'),
           'unrelated_host_socket':socket_result(socket.AF_UNIX,sys.argv[1]),
           'docker_socket':socket_result(socket.AF_UNIX,'/var/run/docker.sock'),
           'host_loopback':socket_result(socket.AF_INET,('127.0.0.1',int(sys.argv[2]))),
           'external_network':socket_result(socket.AF_INET,('1.1.1.1',443)),
           'host_file_visible':Path('/shared/hans/AGENTS.md').exists(),
           'fixture_source_visible':Path('/fixture/serve_fixture.py').exists(),
           'effective_caps':next(x for x in Path('/proc/self/status').read_text().splitlines() if x.startswith('CapEff:')).split()[-1]}
    try:
        Path('/root_write_probe').write_text('not allowed')
        value['root_write']='allowed'
    except OSError as exc:
        value['root_write']=type(exc).__name__
    value['browser']=asyncio.run(browse())
    print(json.dumps(value))


if __name__=='__main__': main()
