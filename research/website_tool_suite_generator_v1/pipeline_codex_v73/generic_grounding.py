"""Site-independent, deliberately non-certifying live grounding diagnostic.

Finding a value on a page is not proof that it answers the intended question,
that a workflow completed, or that a source chosen by the tool is relevant.
This module therefore never returns a semantic-verification pass.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright


def normalize(value: str) -> str:
    return ' '.join(value.casefold().split())


def explicit_aria_fields(candidate: Any) -> set[str]:
    """Find explicitly named top-level ARIA attributes in a candidate schema."""
    returns = candidate.get('returns') if isinstance(candidate, dict) else None
    if not isinstance(returns, dict):
        return set()
    return {key for key in returns if isinstance(key, str) and
            re.search(r'(?:^|_)aria_(?:label|description)$', key, re.I)}


def scalar_values(value: Any, path: str = '$', depth: int = 0) -> list[tuple[str, str]]:
    """Enumerate bounded scalar leaves; reject shapes that lack evidence."""
    if depth > 10 or isinstance(value, (dict, list)) and len(value) > 100:
        raise ValueError('result exceeds structural bounds')
    if isinstance(value, dict):
        leaves = [leaf for key, item in value.items()
                  for leaf in scalar_values(item, f'{path}.{key}', depth + 1)]
    elif isinstance(value, list):
        leaves = [leaf for index, item in enumerate(value)
                  for leaf in scalar_values(item, f'{path}[{index}]', depth + 1)]
    elif type(value) in (str, int, float, bool):
        text = str(value).strip()
        leaves = [(path, text)] if text else []
    else:
        leaves = []
    if len(leaves) > 100:
        raise ValueError('more than 100 scalar output values')
    return leaves


def integer_leaves(value: Any, path: str = '$') -> dict[str, int]:
    """Identify integer leaves after scalar_values has checked structural bounds."""
    if isinstance(value, dict):
        return {key: number for name, item in value.items()
                for key, number in integer_leaves(item, f'{path}.{name}').items()}
    if isinstance(value, list):
        return {key: number for index, item in enumerate(value)
                for key, number in integer_leaves(item, f'{path}[{index}]').items()}
    return {path: value} if type(value) is int else {}


def integer_text_match(text: str, value: int) -> tuple[bool, bool]:
    """Match whole integers and conventional comma groups, never partial numbers."""
    plain, grouped = str(value), f'{value:,}'
    def contains(token: str) -> bool:
        return bool(re.search(r'(?<![\w,.\-])' + re.escape(token) + r'(?![\w,]|\.\d)', text))
    if contains(plain):
        return True, False
    return (True, True) if grouped != plain and contains(grouped) else (False, False)


def allowed_source(start_url: str, source_url: str) -> bool:
    start = urlparse(start_url)
    source = urlparse(source_url)
    return (start.scheme == source.scheme == 'https' and bool(start.hostname)
            and source.hostname == start.hostname and not source.username
            and not source.password and source.port in (None, 443))


async def observe(start_url: str, source_url: str) -> dict:
    if not allowed_source(start_url, source_url):
        raise ValueError('source is not an HTTPS page on the input host')
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={'width': 1280, 'height': 1200})
            response = await page.goto(source_url, wait_until='domcontentloaded', timeout=45000)
            if response is None or response.status != 200 or not allowed_source(start_url, page.url):
                raise ValueError('source did not load as a first-party HTTP 200 page')
            text = (await page.locator('body').inner_text(timeout=15000))[:400000]
            hrefs = await page.locator('a[href]').evaluate_all(
                'es => es.slice(0, 3000).map(e => e.getAttribute("href"))')
            links = [urljoin(page.url, href) for href in hrefs if isinstance(href, str)]
            ids = await page.locator('[id]').evaluate_all(
                'es => es.slice(0, 3000).map(e => e.getAttribute("id"))')
            aria_values = await page.locator('[aria-label], [aria-description]').evaluate_all('''es => {
              const out = {'aria-label': [], 'aria-description': []};
              for (const e of es.slice(0, 3000)) for (const name of Object.keys(out)) {
                const v = e.getAttribute(name); if (v) out[name].push(v);
              }
              return out;
            }''')
            numeric_nodes = await page.locator('body').evaluate('''root => {
              const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
              const out = [];
              while (walker.nextNode() && out.length < 3000) {
                const node = walker.currentNode, parent = node.parentElement;
                const value = (node.nodeValue || '').trim();
                if (!parent || !value || value.length > 200 || !/\\d/.test(value)) continue;
                const style = getComputedStyle(parent);
                if (parent.getClientRects().length && style.visibility !== 'hidden' && style.display !== 'none')
                  out.push(value);
              }
              return out;
            }''')
            return {'page_url': page.url, 'visible_text': text, 'links': links,
                    'ids': [value for value in ids if isinstance(value, str) and value],
                    'numeric_text_nodes': numeric_nodes, 'aria_attribute_values': aria_values}
        finally:
            await browser.close()


async def check(start_url: str, results: dict[str, dict]) -> dict:
    cases = {}
    labels = ['default', 'held'] + [label for label in ('stress_boundary', 'secondary_held')
                                    if label in results]
    for label in labels:
        try:
            result = results[label]
            source = result.get('source_url')
            if not isinstance(source, str) or not allowed_source(start_url, source):
                raise ValueError('missing or unsupported first-party source_url')
            leaves = scalar_values(result.get('result'))
            if not leaves:
                raise ValueError('no nonempty scalar result values')
            observed = await observe(start_url, source)
            text = normalize(observed['visible_text'])
            links = {normalize(link) for link in observed['links']}
            ids = set(observed.get('ids', []))
            integers = integer_leaves(result.get('result'))
            parameters = result.get('parameters') if isinstance(result.get('parameters'), dict) else {}
            body = result.get('result') if isinstance(result.get('result'), dict) else {}
            parameter_echo_paths = [f'$.{key}' for key, value in body.items()
                                    if key in parameters and type(value) in (str, int, float, bool)
                                    and type(value) is type(parameters[key]) and value == parameters[key]]
            matched_formatted_integer_paths = []
            matched_numeric_node_paths = []
            visible_matches = {}
            for path, value in leaves:
                if path in integers:
                    visible_matches[path], formatted = integer_text_match(text, integers[path])
                    if not visible_matches[path]:
                        for node in observed.get('numeric_text_nodes', []):
                            if not isinstance(node, str):
                                continue
                            found, formatted = integer_text_match(normalize(node), integers[path])
                            if found:
                                visible_matches[path] = True
                                matched_numeric_node_paths.append(path)
                                break
                    if formatted:
                        matched_formatted_integer_paths.append(path)
                else:
                    visible_matches[path] = normalize(value) in text
            matched_dom_id_paths = [path for path, value in leaves
                                    if re.fullmatch(r'(?:id|[a-z0-9_]+_id)', path.rsplit('.', 1)[-1], re.I)
                                    and value in ids and path not in parameter_echo_paths]
            # Relative first-party hrefs are equivalent to observed absolute
            # links for existence only. Matching any anchor still does not
            # establish that it answers the question or is visible.
            matched_relative_link_paths = [path for path, value in leaves
                if re.search(r'(?:^|_)(?:href|url)$', path.rsplit('.', 1)[-1], re.I)
                and value.startswith('/') and not value.startswith('//') and '\\' not in value
                and path not in parameter_echo_paths
                and allowed_source(start_url, urljoin(observed['page_url'], value))
                and normalize(urljoin(observed['page_url'], value)) in links]
            matched_formatted_integer_paths = [path for path in matched_formatted_integer_paths
                                               if path not in parameter_echo_paths]
            matched_numeric_node_paths = [path for path in matched_numeric_node_paths
                                          if path not in parameter_echo_paths]
            # A field explicitly named as an ARIA attribute must match that
            # actual attribute, not just similar button text or another field.
            # Matching anywhere is existence evidence, not element relevance.
            aria_fields = {}
            for path, value in leaves:
                field = path.rsplit('.', 1)[-1].casefold()
                for suffix, attribute in (('aria_label', 'aria-label'), ('aria_description', 'aria-description')):
                    if field == suffix or field.endswith('_' + suffix):
                        aria_fields[path] = (value, attribute)
            observed_aria = observed.get('aria_attribute_values', {})
            if not isinstance(observed_aria, dict):
                observed_aria = {}
            matched_aria = [path for path, (value, attribute) in aria_fields.items()
                            if path not in parameter_echo_paths and normalize(value) in {
                                normalize(v) for v in observed_aria.get(attribute, []) if isinstance(v, str)}]
            missing = [path for path, value in leaves if path not in parameter_echo_paths
                       and ((path in aria_fields and path not in matched_aria)
                            or (path not in aria_fields and not visible_matches[path]
                                and normalize(value) not in links and path not in matched_dom_id_paths
                                and path not in matched_relative_link_paths))]
            # Input echoes are not page evidence, even if the same value also
            # happens to occur somewhere on the page.
            if not missing and not any(path not in parameter_echo_paths for path, _ in leaves):
                missing.append('$:no_page_derived_values')
            cases[label] = {'source_url': observed['page_url'], 'checked_values': len(leaves),
                            'missing_value_paths': missing,
                            'matched_dom_id_paths': matched_dom_id_paths,
                            'matched_relative_link_paths': matched_relative_link_paths,
                            'matched_aria_attribute_paths': matched_aria,
                            'matched_formatted_integer_paths': matched_formatted_integer_paths,
                            'matched_numeric_node_paths': matched_numeric_node_paths,
                            'matched_parameter_echo_paths': parameter_echo_paths,
                            'status': 'missing_values' if missing else 'values_found'}
        except (KeyError, TypeError, ValueError, OSError) as error:
            cases[label] = {'status': 'error', 'reason': f'{type(error).__name__}: {error}'}
        except Exception as error:
            # Browser failures are not success and should not abort the loop.
            cases[label] = {'status': 'error', 'reason': f'{type(error).__name__}: {error}'}
    return {'status': 'grounding_only_not_verified', 'cases': cases,
            'limitation': 'Source relevance, field meaning, task completion, and correctness are not established.'}
