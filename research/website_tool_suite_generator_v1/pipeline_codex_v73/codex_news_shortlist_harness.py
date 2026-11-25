#!/usr/bin/env python3
"""Run a copied news script against a locally shortened in-browser headline list.

Execute only inside the project's generated-code sandbox. This mutates the
local browser DOM after navigation; it sends no website state-changing action.
"""
from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import Page


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--script', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--position', required=True, type=int)
    parser.add_argument('--keep', type=int, default=2)
    parser.add_argument('--inject-nested', action='store_true', help='insert a synthetic nested list item locally')
    parser.add_argument('--remove-first-list', action='store_true', help='remove the first headline list locally')
    args = parser.parse_args()
    if args.keep < 0 or args.keep > 4:
        parser.error('keep must be between 0 and 4')
    original = Page.goto

    async def shortened_goto(page, *pargs, **kwargs):
        response = await original(page, *pargs, **kwargs)
        if urlparse(page.url).hostname != 'en.wikipedia.org':
            raise ValueError('unexpected website in local-only mutation test')
        result = await page.evaluate('''options => {
          const {keep, injectNested, removeFirst} = options;
          const section = document.querySelector('#mp-itn');
          const list = section && section.querySelector('ul');
          if (!list) throw new Error('first news list missing');
          const items = [...list.querySelectorAll(':scope > li')];
          if (removeFirst) {
            list.remove();
            return {original: items.length, first_list_removed: true};
          }
          if (items.length < keep) throw new Error('not enough live items for fixture');
          items.slice(keep).forEach(item => item.remove());
          if (injectNested) {
            if (!items[0]) throw new Error('cannot nest inside empty list');
            const nested = document.createElement('ul'), li = document.createElement('li');
            const link = document.createElement('a');
            link.href = '/wiki/Synthetic_nested_note';
            link.textContent = 'SYNTHETIC NESTED NOTE';
            li.appendChild(link); nested.appendChild(li); items[0].appendChild(nested);
          }
          return {original: items.length, kept: list.querySelectorAll(':scope > li').length,
                  injected_nested: injectNested};
        }''', {'keep': args.keep, 'injectNested': args.inject_nested,
                'removeFirst': args.remove_first_list})
        print(f'local-only shortened headline fixture: {result}', flush=True)
        return response

    Page.goto = shortened_goto
    sys.argv = [str(args.script), '--position', str(args.position), '--output-dir', str(args.output_dir)]
    runpy.run_path(str(args.script), run_name='__main__')


if __name__ == '__main__':
    main()
