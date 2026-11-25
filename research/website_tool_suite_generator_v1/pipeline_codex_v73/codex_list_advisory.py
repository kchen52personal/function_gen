"""Conservative, non-certifying structural feedback for disclosed list cases.

The comparison is only a heuristic: filtering, ranking, or dynamic content may
make the first DOM list a poor oracle. It never decides verification itself.
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from playwright.async_api import async_playwright
from generic_grounding import allowed_source


def compare_prefix(items: list[str], containers: list[list[str]], limit: int) -> dict[str, Any]:
    def clean(value: str) -> str:
        return ' '.join(value.split())
    if (type(limit) is not int or not 2 <= limit <= 20 or len(items) < 2
            or not all(isinstance(item, str) and item.strip() for item in items)):
        return {'status': 'inapplicable'}
    actual = [clean(item) for item in items]
    if actual[0] == actual[1]:
        return {'status': 'ambiguous'}
    matches = []
    for container in containers:
        if not isinstance(container, list) or len(container) < 2 or not all(isinstance(x, str) for x in container):
            continue
        values = [clean(item) for item in container]
        if values[:2] == actual[:2]:
            matches.append(values)
    if len(matches) != 1:
        return {'status': 'ambiguous', 'matching_lists': len(matches)}
    expected = matches[0][:limit]
    if actual == expected:
        return {'status': 'consistent', 'expected_count': len(expected)}
    return {'status': 'mismatch', 'expected_count': len(expected), 'actual_count': len(actual),
            'expected_items': [item[:600] for item in expected[:20]],
            'limitation': 'Heuristic only: the requested task may intentionally filter or reorder the DOM list.'}


async def inspect(start_url: str, source_url: str, items: list[str], limit: int) -> dict[str, Any]:
    if not allowed_source(start_url, source_url):
        return {'status': 'unavailable', 'reason': 'not a first-party HTTPS source'}
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={'width': 1280, 'height': 1200})
                response = await page.goto(source_url, wait_until='domcontentloaded', timeout=30000)
                if response is None or response.status != 200 or not allowed_source(start_url, page.url):
                    raise ValueError('source did not load as a first-party HTTP 200 page')
                containers = await page.locator('ul,ol').evaluate_all('''lists => lists.slice(0, 200).map(list =>
                  Array.from(list.children).filter(e => e.tagName === 'LI' && e.getClientRects().length)
                    .slice(0, 50).map(e => (e.innerText || '').trim().slice(0, 2000)))''')
                return compare_prefix(items, containers, limit)
            finally:
                await browser.close()
    except Exception as error:
        return {'status': 'unavailable', 'reason': f'{type(error).__name__}: {str(error)[:300]}'}


def main() -> None:
    payload = json.load(sys.stdin)
    print(json.dumps(asyncio.run(inspect(payload['start_url'], payload['source_url'],
                                         payload['items'], payload['limit'])), ensure_ascii=False))


if __name__ == '__main__':
    main()
