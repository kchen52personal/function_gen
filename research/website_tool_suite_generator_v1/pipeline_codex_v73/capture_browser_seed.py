#!/usr/bin/env python3
"""Capture bounded public-page DOM evidence for a WebWright attempt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


SHARED_ROOT = Path("/shared/hans").resolve()


def _inside_shared(path: Path) -> Path:
    resolved = path.resolve()
    if resolved != SHARED_ROOT and SHARED_ROOT not in resolved.parents:
        raise ValueError("output must stay inside /shared/hans")
    return resolved


def _bounded_screenshot(page, path: Path) -> None:
    """Capture the fixed viewport, retrying once after transient CDP errors."""

    try:
        page.screenshot(path=path, animations="disabled", timeout=30_000)
    except Exception:
        page.wait_for_timeout(1000)
        page.screenshot(
            path=path,
            clip={"x": 0, "y": 0, "width": 1280, "height": 1800},
            animations="disabled",
            timeout=30_000,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--fill-selector")
    parser.add_argument("--fill-value")
    parser.add_argument("--press", default="Enter")
    parser.add_argument("--wait-ms", type=int, default=3000)
    args = parser.parse_args()

    parsed = urlparse(args.url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("only an explicit public HTTPS URL is allowed")
    if bool(args.fill_selector) != bool(args.fill_value):
        raise ValueError("fill-selector and fill-value must be supplied together")
    if not 0 <= args.wait_ms <= 10_000:
        raise ValueError("wait-ms must be between 0 and 10000")

    output = _inside_shared(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError(f"output directory is not empty: {output}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 1800})
        page = context.new_page()
        page.goto(args.url, wait_until="domcontentloaded", timeout=60_000)
        _bounded_screenshot(page, output / "before.png")
        if args.fill_selector:
            field = page.locator(args.fill_selector)
            if field.count() != 1:
                raise RuntimeError(
                    f"expected exactly one fill target, found {field.count()}"
                )
            field.fill(args.fill_value)
            field.press(args.press)
            page.wait_for_load_state("domcontentloaded", timeout=60_000)
        page.wait_for_timeout(args.wait_ms)
        _bounded_screenshot(page, output / "after.png")

        records = page.locator(
            "main, [role=main], h1, h2, h3, input, button, form, "
            "[role=list], [role=listitem], article, section, a[href]"
        ).evaluate_all(
            """nodes => nodes.slice(0, 800).map((node, index) => ({
              index,
              tag: node.tagName.toLowerCase(),
              id: node.id || '',
              classes: String(node.className || '').slice(0, 300),
              role: node.getAttribute('role') || '',
              name: node.getAttribute('name') || '',
              type: node.getAttribute('type') || '',
              aria_label: node.getAttribute('aria-label') || '',
              placeholder: node.getAttribute('placeholder') || '',
              href: node.href || '',
              visible: !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length),
              text: String(node.innerText || node.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 1000),
              html: String(node.outerHTML || '').slice(0, 2500)
            }))"""
        )
        summary = {
            "requested_url": args.url,
            "final_url": page.url,
            "title": page.title(),
            "interaction": {
                "fill_selector": args.fill_selector,
                "fill_value": args.fill_value,
                "press": args.press if args.fill_selector else None,
            },
            "records": records,
        }
        (output / "dom_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (output / "rendered.html").write_text(page.content(), encoding="utf-8")
        try:
            aria = page.locator("body").aria_snapshot(timeout=10_000)
        except Exception as error:  # evidence degrades explicitly, never silently
            aria = f"ARIA snapshot unavailable: {type(error).__name__}: {error}\n"
        (output / "aria_snapshot.txt").write_text(aria, encoding="utf-8")
        (output / "README.md").write_text(
            "# Browser seed\n\n"
            "Fresh public Chromium context. `before.png` and `after.png` are bounded "
            "1280x1800 viewport screenshots. `dom_summary.json`, `aria_snapshot.txt` "
            "and `rendered.html` describe the post-interaction page. This evidence is "
            "for selector discovery only; the final tool must independently rerun and "
            "verify the interaction.\n",
            encoding="utf-8",
        )
        browser.close()

    print(json.dumps({
        "status": "ok",
        "output_dir": str(output),
        "final_url": summary["final_url"],
        "records": len(records),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
