#!/usr/bin/env python3
"""Bounded read-only reconnaissance for URL-to-tool-suite generation."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urldefrag, urljoin, urlparse, urlunparse

from playwright.async_api import Frame, Page, async_playwright


def _safe_first_party(base: str, href: str, hostname: str) -> str | None:
    value = href.strip()
    if not value or value.startswith(("javascript:", "mailto:", "tel:", "data:")):
        return None
    absolute = urljoin(base, value)
    parsed = urlparse(absolute)
    if parsed.scheme != "https" or parsed.hostname != hostname:
        return None
    # In-page anchors do not identify distinct pages for this bounded crawl.
    return urldefrag(absolute).url


def _route_family(url: str) -> str:
    """Collapse argument values so one parameterized route gets one page slot."""
    parsed = urlparse(url)
    # Normalize common identifier shapes, irrespective of website or domain.
    parts = parsed.path.rstrip('/').split('/')
    path = '/'.join('{id}' if (part.isdigit() or re.fullmatch(r'[0-9a-fA-F-]{20,}', part))
                    else part for part in parts) or '/'
    keys = sorted({key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)})
    family_query = "&".join(f"{key}={{{key}}}" for key in keys)
    return urlunparse((
        parsed.scheme.casefold(),
        parsed.netloc.casefold(),
        path,
        "",
        family_query,
        "",
    ))


def _candidate_priority(item: dict[str, object]) -> tuple[object, ...]:
    text = f"{item.get('text', '')} {item.get('title', '')} {item.get('aria_label', '')} {item.get('image_alt', '')}".casefold()
    url = str(item["url"]).casefold()
    blocked_terms = ("login", "logout", "sign in", "sign out", "sign up", "create account",
                     "register", "checkout", "purchase", "delete", "upload", "submit", "donate",
                     "cookie", "privacy policy", "terms and conditions", "accessibility statement",
                     "action=edit", "登录", "退出", "支付", "删除")
    blocked = any(term in text or term in url for term in blocked_terms)
    return (
        blocked,
        not bool(text.strip()),
        not bool(item["in_primary_chrome"]),
        int(item["order"]),
        url,
    )


async def _records(frame: Frame, selector: str, limit: int) -> list[dict[str, object]]:
    return await frame.locator(selector).evaluate_all(
        r"""(elements, limit) => elements
            .filter(el => el.offsetParent !== null)
            .slice(0, limit)
            .map((el, order) => ({
                order,
                tag: el.tagName.toLowerCase(),
                text: (el.innerText || el.value || '').replace(/\s+/g, ' ').trim().slice(0, 300),
                href: el.getAttribute('href') || '',
                title: el.getAttribute('title') || '',
                aria_label: el.getAttribute('aria-label') || '',
                image_alt: el.querySelector('img[alt]')?.getAttribute('alt') || '',
                name: el.getAttribute('name') || '',
                type: el.getAttribute('type') || '',
                role: el.getAttribute('role') || '',
                in_primary_chrome: Boolean(el.closest('nav,header,[role="navigation"]')),
                in_form: Boolean(el.closest('form'))
            }))""",
        limit,
    )


async def _inspect(page: Page, url: str, screenshot: Path, hostname: str) -> dict:
    errors: list[str] = []
    page.on(
        "console",
        lambda message: errors.append(message.text[:500])
        if message.type == "error"
        else None,
    )
    response = await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(1_500)
    frames = []
    for frame in page.frames:
        frame_host = urlparse(frame.url).hostname
        if frame == page.main_frame or frame_host == hostname:
            try:
                await frame.locator("body").wait_for(state="visible", timeout=10_000)
                frames.append({
                    "url": frame.url,
                    "headings": await _records(frame, "h1,h2,h3", 50),
                    "links": await _records(frame, "a[href]", 160),
                    "actions": await _records(
                        frame,
                        "button,input[type=submit],input[type=button],[role=button]",
                        80,
                    ),
                    "fields": await _records(frame, "input,textarea,select", 80),
                    "forms": await _records(frame, "form", 30),
                    "content": await _records(
                        frame,
                        (
                            "main p,main li,main td,main th,article p,article li,"
                            "article td,article th,#content p,#content li,"
                            "#content td,#content th"
                        ),
                        160,
                    ),
                })
            except Exception as error:
                frames.append({
                    "url": frame.url,
                    "probe_error": type(error).__name__,
                })
    await page.screenshot(path=screenshot)
    return {
        "requested_url": url,
        "final_url": page.url,
        "http_status": response.status if response else None,
        "title": await page.title(),
        "frames": frames,
        "console_errors": errors[:30],
    }


def _candidate_links(page_record: dict, hostname: str) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for frame in page_record["frames"]:
        frame_url = frame.get("url", page_record["final_url"])
        for item in frame.get("links", []):
            target = _safe_first_party(frame_url, str(item.get("href", "")), hostname)
            if not target:
                continue
            output.append({
                "url": target,
                "text": item.get("text", ""),
                "title": item.get("title", ""),
                "aria_label": item.get("aria_label", ""),
                "image_alt": item.get("image_alt", ""),
                "in_primary_chrome": bool(item.get("in_primary_chrome")),
                "source_url": page_record["final_url"],
                "order": item.get("order", 9999),
            })
    return output


async def reconnaissance(
    start_url: str, output_dir: Path, max_pages: int = 8
) -> dict[str, object]:
    parsed = urlparse(start_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("start_url must be an absolute HTTPS URL")
    if not 1 <= max_pages <= 12:
        raise ValueError("max_pages must be between 1 and 12")
    hostname = parsed.hostname
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshots = output_dir / "screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)

    queue = [start_url]
    queued = {start_url}
    queued_families = {_route_family(start_url)}
    visited: set[str] = set()
    pages: list[dict[str, object]] = []
    link_occurrences: Counter[str] = Counter()
    link_texts: dict[str, set[str]] = {}

    async with async_playwright() as playwright:
        browser = await playwright.firefox.launch(headless=True)
        try:
            while queue and len(pages) < max_pages:
                url = queue.pop(0)
                visited.add(url)
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 1800}
                )
                page = await context.new_page()
                try:
                    digest = hashlib.sha256(url.encode()).hexdigest()[:10]
                    record = await _inspect(
                        page,
                        url,
                        screenshots / f"page_{len(pages):02d}_{digest}.png",
                        hostname,
                    )
                except Exception as error:
                    record = {
                        "requested_url": url,
                        "final_url": page.url,
                        "probe_error": type(error).__name__,
                        "error_message": str(error)[:500],
                        "frames": [],
                    }
                finally:
                    await context.close()
                pages.append(record)
                candidates = _candidate_links(record, hostname)
                for item in candidates:
                    target = str(item["url"])
                    link_occurrences[target] += 1
                    label = str(item.get("text") or item.get("title") or "").strip()
                    if label:
                        link_texts.setdefault(target, set()).add(label)
                candidates.sort(key=_candidate_priority)
                for item in candidates:
                    target = str(item["url"])
                    family = _route_family(target)
                    if (
                        target not in visited
                        and target not in queued
                        and family not in queued_families
                    ):
                        queue.append(target)
                        queued.add(target)
                        queued_families.add(family)

        finally:
            await browser.close()

    repeated_routes = [
        {
            "url": url,
            "occurrences": count,
            "labels": sorted(link_texts.get(url, set()))[:10],
        }
        for url, count in link_occurrences.most_common(80)
    ]
    result: dict[str, object] = {
        "start_url": start_url,
        "allowed_host": hostname,
        "max_pages": max_pages,
        "pages_attempted": len(pages),
        "pages": pages,
        "repeated_first_party_routes": repeated_routes,
        "boundary": (
            "Read-only bounded reconnaissance. Visible controls and routes are "
            "candidate evidence, not proof that a complete functionality works."
        ),
    }
    (output_dir / "recon.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("start_url")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=8)
    args = parser.parse_args()
    result = asyncio.run(
        reconnaissance(args.start_url, args.output_dir, args.max_pages)
    )
    print(json.dumps({
        "start_url": result["start_url"],
        "pages_attempted": result["pages_attempted"],
        "repeated_routes": len(result["repeated_first_party_routes"]),
        "output": str(args.output_dir / "recon.json"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
