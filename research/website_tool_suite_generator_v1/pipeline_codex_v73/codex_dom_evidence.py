"""Capture bounded live markup around proposal-cited first-party evidence."""
from __future__ import annotations

import re
import asyncio
from typing import Any

from playwright.async_api import async_playwright

from generic_grounding import allowed_source


DOM_CAPTURE_JS = r'''(elements, attrs) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const keys = [['text', e => e.innerText], ['title', e => e.getAttribute('title')],
    ['aria_label', e => e.getAttribute('aria-label')],
    ['name', e => e.getAttribute('name')], ['href', e => e.getAttribute('href')]];
  const relevant = keys.filter(([key]) => attrs[key]);
  const match = (e, checks) => checks.every(([key, get]) => norm(get(e)) === norm(attrs[key]));
  let selected = elements.filter(e => match(e, relevant));
  let method = 'all_attributes';
  // Browser/platform shortcut hints in title can change. Ignore title only
  // when both the field name and accessible name still identify one element.
  if (!selected.length && attrs.name && attrs.aria_label && attrs.title) {
    const withoutTitle = relevant.filter(([key]) => key !== 'title');
    const fallback = elements.filter(e => match(e, withoutTitle));
    if (fallback.length === 1) {
      selected = fallback;
      method = 'unique_name_and_aria_label_without_title';
    }
  }
  return selected.slice(0, 3).map(e => {
    const parent = e.parentElement;
    const section = e.closest('section') || parent;
    const ancestorHeadings = [];
    let nearestHeading = '';
    let current = section;
    for (let depth = 0; current && depth < 4; depth++) {
      const ids = typeof current.getAttribute === 'function' ?
        (current.getAttribute('aria-labelledby') || '').trim().split(/\s+/).filter(Boolean) : [];
      const doc = e.ownerDocument;
      let label = ids.length && doc ? doc.getElementById(ids[0]) : null;
      if (!label && typeof current.querySelector === 'function')
        label = current.querySelector(':scope > h1, :scope > h2, :scope > h3, :scope > h4, :scope > h5, :scope > h6');
      const text = label ? (label.innerText || label.textContent || '').trim().slice(0, 200) : '';
      if (depth === 0) nearestHeading = text;
      if (text) ancestorHeadings.push(text);
      const above = current.parentElement;
      current = above && typeof above.closest === 'function' ? above.closest('section') : null;
    }
    const form = e.closest('form');
    let formSummary = null;
    if (form) {
      let action = '';
      try { const u = new URL(form.action); action = u.origin + u.pathname; } catch (_) {}
      formSummary = {action_without_query: action.slice(0, 500),
        method: (form.method || '').slice(0, 30),
        controls: Array.from(form.querySelectorAll('input,button,select,textarea')).slice(0, 30)
          .map(control => ({tag: control.tagName.toLowerCase(),
            name: (control.getAttribute('name') || '').slice(0, 120),
            type: (control.getAttribute('type') || '').slice(0, 60),
            aria_label: (control.getAttribute('aria-label') || '').slice(0, 200),
            text: control.tagName.toLowerCase() === 'button' ?
              (control.innerText || '').trim().slice(0, 200) : ''}))};
    }
    return {match_method: method, element: e.outerHTML.slice(0, 1500),
      form_summary: formSummary,
      section_context: {heading: nearestHeading, ancestor_headings: ancestorHeadings,
        note: 'bounded ancestor hints only; empty or generic headings do not establish task scope'},
      parent: parent ? parent.outerHTML.slice(0, 5000) : '',
      section: section ? section.outerHTML.slice(0, 9000) : '',
      next_sibling: e.nextElementSibling ? e.nextElementSibling.outerHTML.slice(0, 3000) : '',
      parent_next_sibling: parent && parent.nextElementSibling ?
        parent.nextElementSibling.outerHTML.slice(0, 5000) : ''};
  });
}'''


async def capture(start_url: str, candidate: dict[str, Any]) -> dict[str, Any]:
    evidence = candidate.get('proposal_evidence', {})
    resolved = evidence.get('resolved', {}) if isinstance(evidence, dict) else {}
    record = resolved.get('record', {}) if isinstance(resolved, dict) else {}
    source_url = resolved.get('page_url') if isinstance(resolved, dict) else None
    if not isinstance(record, dict) or not isinstance(source_url, str):
        return {'status': 'unavailable', 'reason': 'no resolved proposal evidence'}
    if not allowed_source(start_url, source_url):
        return {'status': 'unavailable', 'reason': 'evidence page is not first-party HTTPS'}
    tag = str(record.get('tag', '')).lower()
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,30}', tag):
        return {'status': 'unavailable', 'reason': 'evidence has no safe HTML tag'}
    attributes = {key: str(record.get(key, ''))[:300]
                  for key in ('text', 'title', 'aria_label', 'name', 'href')}
    if not any(attributes.values()):
        return {'status': 'unavailable', 'reason': 'evidence has no identifying attributes'}
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={'width': 1280, 'height': 1200})
                response = await page.goto(source_url, wait_until='domcontentloaded', timeout=45000)
                if response is None or response.status != 200 or not allowed_source(start_url, page.url):
                    raise ValueError('evidence page did not load as a first-party HTTP 200 page')
                matches = await page.locator(tag).evaluate_all(DOM_CAPTURE_JS, attributes)
                return {'status': 'captured' if matches else 'no_match',
                        'source_url': page.url, 'matches': matches}
            finally:
                await browser.close()
    except Exception as error:
        return {'status': 'unavailable', 'reason': f'{type(error).__name__}: {str(error)[:500]}'}


async def capture_return_context(start_url: str, candidate: dict[str, Any]) -> dict[str, Any]:
    """Capture small, bounded section hints for up to three declared return fields."""
    evidence = candidate.get('proposal_return_evidence')
    if not isinstance(evidence, dict):
        return {'status': 'unavailable', 'reason': 'no resolved return evidence'}
    fields = list(evidence)[:3]
    async def one(field: str) -> tuple[str, dict]:
        item = evidence[field]
        if not isinstance(item, dict):
            return field, {'status': 'unavailable', 'reason': 'invalid return evidence'}
        try:
            result = await asyncio.wait_for(capture(start_url, {'proposal_evidence': item}), timeout=55)
        except asyncio.TimeoutError:
            return field, {'status': 'unavailable', 'reason': 'return context timed out'}
        return field, {'status': result.get('status'), 'source_url': result.get('source_url'),
                       'matches': [{'element': str(match.get('element', ''))[:500],
                                    'section_context': match.get('section_context', {})}
                                   for match in result.get('matches', [])[:3] if isinstance(match, dict)]}
    pairs = await asyncio.gather(*(one(field) for field in fields))
    return {'status': 'captured' if any(item.get('status') == 'captured' for _, item in pairs) else 'unavailable',
            'fields': dict(pairs), 'truncated': len(evidence) > 3,
            'limitation': 'Context is advisory and may be missing or ambiguous; it is not a scope proof.'}
