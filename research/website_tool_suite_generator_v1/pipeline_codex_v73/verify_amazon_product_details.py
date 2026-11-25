"""Compare a generated product-details tool against fresh Amazon pages."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from playwright.async_api import async_playwright
from result_log import read_result

VERIFICATION_VERSION = 'amazon_product_details_full_spec_v2'


def normalize(value: str) -> str:
    return ' '.join(value.split())


async def live_reference(asin: str) -> dict[str, Any]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={'width': 1280, 'height': 1800})
            response = await page.goto(f'https://www.amazon.sg/dp/{asin}',
                                       wait_until='domcontentloaded', timeout=60000)
            if response is None or response.status != 200:
                raise RuntimeError(f'product page returned HTTP {response.status if response else "none"}')
            async def text(selector: str) -> str:
                element = page.locator(selector).first
                if not await element.count():
                    raise RuntimeError(f'product field missing: {selector}')
                value = normalize(await element.text_content() or '')
                if not value:
                    raise RuntimeError(f'product field empty: {selector}')
                return value
            features = [normalize(x) for x in await page.locator('#feature-bullets li span.a-list-item').all_text_contents()]
            features = [x for x in features if len(x) >= 12]
            if not features:
                raise RuntimeError('product features absent')
            # Amazon often hides additional specification tables under collapsed
            # headings. They are part of the requested full specifications.
            expanders = page.locator('#productDetails_expanderSectionTables a.a-expander-header')
            if await expanders.count() > 20:
                raise RuntimeError('too many specification sections to inspect safely')
            expander_evidence = await expanders.evaluate_all('''els => els.map(e => ({
                text: (e.innerText || '').slice(0, 250),
                html: e.outerHTML.slice(0, 900)
            }))''')
            for i in range(await expanders.count()):
                expander = expanders.nth(i)
                if await expander.get_attribute('aria-expanded') == 'false':
                    await expander.click(timeout=10000)
                    if await expander.get_attribute('aria-expanded') != 'true':
                        raise RuntimeError('could not open product information section')
            specifications = {}
            rows = page.locator('#productOverview_feature_div tr, '
                                '#productDetails_techSpec_section_1 tr, '
                                '#productDetails_expanderSectionTables table.prodDetTable tr')
            for i in range(await rows.count()):
                cells = [normalize(x) for x in await rows.nth(i).locator('th,td').all_inner_texts()]
                if len(cells) >= 2 and cells[0] and cells[1]:
                    specifications[cells[0]] = ' '.join(cells[1:])
            if not specifications:
                raise RuntimeError('product specification table absent')
            # Concrete browser evidence helps a subsequent repair distinguish a
            # current price from a crossed-out list price. This is gathered by
            # the verifier, not asserted by the generated program.
            price_evidence = await page.locator(
                '#corePriceDisplay_desktop_feature_div .a-price'
            ).evaluate_all('''els => els.slice(0, 6).map(e => ({
                html: e.outerHTML.slice(0, 1400),
                surrounding_text: (e.parentElement.innerText || '').slice(0, 400)
            }))''')
            spec_evidence = await rows.evaluate_all('''els => els.slice(0, 20).map(e => ({
                text: (e.innerText || '').slice(0, 400),
                html: e.outerHTML.slice(0, 700)
            }))''')
            return {'asin': asin, 'title': await text('#productTitle'),
                    # Fail closed if there is no identifiable current price;
                    # the first generic .a-price can be a struck-through price.
                    'price': await text('#corePriceDisplay_desktop_feature_div '
                                        '.priceToPay:not(.a-text-price):not([data-a-strike="true"])'),
                    'availability': await text('#availability'),
                    'seller': await text('#sellerProfileTriggerId'), 'features': features,
                    'specifications': specifications,
                    'dom_evidence': {'price_candidates': price_evidence,
                                     'expander_controls': expander_evidence,
                                     'specification_rows': spec_evidence}}
        finally:
            await browser.close()


async def verify(run_dirs: dict[str, Path], cases: dict[str, str]) -> dict[str, Any]:
    errors = []
    comparisons = {}
    references = {}
    if set(cases) != {'default', 'held'} or cases['default'] == cases['held']:
        return {'status': 'fail', 'errors': ['two different product IDs are required']}
    for label in ('default', 'held'):
        run_dir = run_dirs[label]
        try:
            result = read_result(run_dir / 'final_script_log.txt')
            for name in ('before.png', 'after.png'):
                if not (run_dir / 'screenshots' / name).is_file() or not (run_dir / 'screenshots' / name).stat().st_size:
                    raise ValueError(f'missing screenshot {name}')
            reference = await live_reference(cases[label])
            references[label] = reference
            for field in ('asin', 'title', 'availability', 'seller'):
                if not isinstance(result.get(field), str) or normalize(result[field]) != normalize(reference[field]):
                    errors.append(f'{label}: {field} differs from fresh live product page')
            price = re.search(r'(\d[\d,]*(?:\.\d+)?)', reference['price'])
            if (price is None or type(result.get('price')) not in (int, float)
                    or float(result['price']) != float(price.group(1).replace(',', ''))):
                errors.append(f'{label}: numeric price differs from fresh live product page')
            features = result.get('features')
            if not isinstance(features, list) or not features:
                errors.append(f'{label}: missing features')
            elif any(not isinstance(x, str) or normalize(x) not in reference['features'] for x in features):
                errors.append(f'{label}: feature not found on fresh live product page')
            specifications = result.get('specifications')
            observed_specs = ({normalize(str(k)): normalize(str(v)) for k, v in specifications.items()}
                              if isinstance(specifications, dict) else {})
            if observed_specs != reference['specifications']:
                errors.append(f'{label}: specifications differ from fresh live product page')
            comparisons[label] = {'asin': cases[label], 'reference_fields': list(reference),
                                  'reference_feature_count': len(reference['features']),
                                  'specification_missing': {
                                      k: v for k, v in reference['specifications'].items()
                                      if k not in observed_specs},
                                  'specification_extra': {
                                      k: v for k, v in observed_specs.items()
                                      if k not in reference['specifications']},
                                  'specification_mismatched': {
                                      k: {'expected': v, 'observed': observed_specs[k]}
                                      for k, v in reference['specifications'].items()
                                      if k in observed_specs and observed_specs[k] != v}}
        except Exception as exc:
            errors.append(f'{label}: {type(exc).__name__}: {exc}')
    return {'status': 'fail' if errors else 'pass', 'errors': errors,
            'verification_version': VERIFICATION_VERSION,
            'comparisons': comparisons, 'fresh_live_reference': references}
