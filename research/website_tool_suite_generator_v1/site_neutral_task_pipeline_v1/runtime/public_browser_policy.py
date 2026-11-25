"""Playwright transport configuration only; the network namespace is isolated.

The command can override these Python defaults, but cannot gain direct network
access: --network none still applies. Does not modify page data or task logic.
"""
import copy
import json
import os
from pathlib import Path
from playwright.async_api import BrowserType as AsyncBrowserType, Browser as AsyncBrowser
from playwright.sync_api import BrowserType as SyncBrowserType, Browser as SyncBrowser

PROXY = {'server': 'http://127.0.0.1:18766', 'bypass': '<-loopback>'}
_async_launch = AsyncBrowserType.launch
_async_persistent = AsyncBrowserType.launch_persistent_context
_sync_launch = SyncBrowserType.launch
_sync_persistent = SyncBrowserType.launch_persistent_context
_async_context = AsyncBrowser.new_context
_async_page = AsyncBrowser.new_page
_sync_context = SyncBrowser.new_context
_sync_page = SyncBrowser.new_page


def private_state():
    path = os.environ.get('WW_BROWSER_SESSION_STATE')
    if not path:
        return None
    # Only the host-selected ephemeral container file; never a workspace artifact.
    if path != '/tmp/ww_private_session/state.json':
        raise RuntimeError('invalid browser session location')
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        raise RuntimeError('private browser session unavailable') from None


def context_options(kwargs):
    state = private_state()
    if state is not None:
        kwargs['storage_state'] = copy.deepcopy(state)
    return kwargs


def local_storage_script(state):
    # Origin-scoped initialization; no answers, selectors, or task-specific logic.
    return ('const baseline = '+json.dumps(state.get('origins', []))+'; '
            'for (const item of baseline) { if (location.origin === item.origin) { '
            'for (const entry of item.localStorage || []) localStorage.setItem(entry.name, entry.value); }}')


async def async_context(self, *args, **kwargs):
    return await _async_context(self, *args, **context_options(kwargs))


async def async_page(self, *args, **kwargs):
    return await _async_page(self, *args, **context_options(kwargs))


def sync_context(self, *args, **kwargs):
    return _sync_context(self, *args, **context_options(kwargs))


def sync_page(self, *args, **kwargs):
    return _sync_page(self, *args, **context_options(kwargs))


async def async_launch(self, *args, **kwargs):
    kwargs['proxy'] = PROXY
    return await _async_launch(self, *args, **kwargs)


async def async_persistent(self, *args, **kwargs):
    kwargs['proxy'] = PROXY
    context = await _async_persistent(self, *args, **kwargs)
    state = private_state()
    if state is not None:
        await context.add_cookies(state['cookies'])
        await context.add_init_script(script=local_storage_script(state))
    return context


def sync_launch(self, *args, **kwargs):
    kwargs['proxy'] = PROXY
    return _sync_launch(self, *args, **kwargs)


def sync_persistent(self, *args, **kwargs):
    kwargs['proxy'] = PROXY
    context = _sync_persistent(self, *args, **kwargs)
    state = private_state()
    if state is not None:
        context.add_cookies(state['cookies'])
        context.add_init_script(script=local_storage_script(state))
    return context


AsyncBrowserType.launch = async_launch
AsyncBrowserType.launch_persistent_context = async_persistent
SyncBrowserType.launch = sync_launch
SyncBrowserType.launch_persistent_context = sync_persistent
AsyncBrowser.new_context = async_context
AsyncBrowser.new_page = async_page
SyncBrowser.new_context = sync_context
SyncBrowser.new_page = sync_page
