#!/usr/bin/env python3
"""Independently verify default and held Amazon.sg product-search runs."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import async_playwright
from result_log import read_result

VERIFICATION_VERSION = 'amazon_product_search_all_items_v2'

SHARED_ROOT = Path("/shared/hans").resolve()


def inside_shared(path: Path) -> Path:
    resolved = path.resolve()
    if resolved != SHARED_ROOT and SHARED_ROOT not in resolved.parents:
        raise ValueError("all paths must stay inside /shared/hans")
    return resolved


def load_result(run_dir: Path) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    log_path = run_dir / "final_script_log.txt"
    if not log_path.is_file():
        return {}, [f"missing log: {log_path}"]
    try:
        result = read_result(log_path)
    except (OSError, ValueError) as error:
        return {}, errors + [f"invalid result log: {error}"]
    for name in ("before.png", "after.png"):
        screenshot = run_dir / "screenshots" / name
        if not screenshot.is_file() or screenshot.stat().st_size == 0:
            errors.append(f"missing non-empty screenshot: {name}")
    return result, errors


def validate_shape(result: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    query = result.get("query")
    limit = result.get("limit")
    products = result.get("products")
    if not isinstance(query, str) or not query.strip():
        errors.append(f"{label}: invalid query")
    if not isinstance(limit, int) or not 1 <= limit <= 20:
        errors.append(f"{label}: invalid limit")
    if not isinstance(products, list) or len(products) != limit:
        errors.append(f"{label}: products length does not equal limit")
        return errors
    asins: list[str] = []
    for index, item in enumerate(products, start=1):
        if not isinstance(item, dict):
            errors.append(f"{label}: product {index} is not an object")
            continue
        asin = item.get("asin")
        title = item.get("title")
        url = item.get("url")
        if not isinstance(asin, str) or not re.fullmatch(r"[A-Z0-9]{10}", asin):
            errors.append(f"{label}: product {index} has invalid ASIN")
        else:
            asins.append(asin)
        if not isinstance(title, str) or not title.strip():
            errors.append(f"{label}: product {index} has empty title")
        if not isinstance(url, str):
            errors.append(f"{label}: product {index} has invalid URL")
        else:
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname != "www.amazon.sg":
                errors.append(f"{label}: product {index} is not an Amazon.sg HTTPS URL")
            if isinstance(asin, str) and f"/dp/{asin}/" not in parsed.path:
                errors.append(f"{label}: product {index} URL does not match its ASIN")
    if len(asins) != len(set(asins)):
        errors.append(f"{label}: duplicate ASINs")
    return errors


async def live_reference(query: str, limit: int) -> list[dict[str, str]]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 1800})
        page = await context.new_page()
        response = await page.goto("https://www.amazon.sg/", wait_until="domcontentloaded", timeout=60_000)
        search = page.locator("input#twotabsearchtextbox")
        try:
            await search.wait_for(state="visible", timeout=15000)
        except Exception as error:
            status = response.status if response is not None else 'no response'
            title = (await page.title())[:160]
            raise RuntimeError(f'live search input unavailable (HTTP {status}, page title {title!r})') from error
        await search.fill(query)
        await search.press("Enter")
        rows = page.locator('div[data-component-type="s-search-result"][data-asin]')
        await rows.first.wait_for(state="visible", timeout=30_000)
        reference: list[dict[str, str]] = []
        for index in range(await rows.count()):
            row = rows.nth(index)
            asin = await row.get_attribute("data-asin")
            title_locator = row.locator('div[data-cy="title-recipe"] h2').first
            if not asin or await title_locator.count() == 0:
                continue
            title = (await title_locator.inner_text()).strip()
            if not title:
                continue
            reference.append({"asin": asin, "title": title})
            if len(reference) == limit:
                break
        await context.close()
        await browser.close()
    if len(reference) != limit:
        raise RuntimeError(f"live reference returned {len(reference)} of {limit} records")
    return reference


async def verify(default_dir: Path, held_dir: Path) -> dict[str, Any]:
    default, default_errors = load_result(default_dir)
    held, held_errors = load_result(held_dir)
    errors = [f"default: {error}" for error in default_errors]
    errors.extend(f"held: {error}" for error in held_errors)
    errors.extend(validate_shape(default, "default"))
    errors.extend(validate_shape(held, "held"))
    live: dict[str, Any] = {}
    comparisons: dict[str, Any] = {}
    if not errors:
        for label, result in (("default", default), ("held", held)):
            reference = await live_reference(result["query"], result["limit"])
            live[label] = reference
            observed_by_asin = {
                item["asin"]: " ".join(item["title"].split())
                for item in result["products"]
            }
            reference_by_asin = {
                item["asin"]: " ".join(item["title"].split())
                for item in reference
            }
            overlap = set(observed_by_asin) & set(reference_by_asin)
            # Every returned item needs independent support; 75% overlap could
            # incorrectly accept one or more fabricated/unrelated products.
            # Ranking and personalization can change, so instability fails
            # closed rather than becoming a claim of complete correctness.
            required_overlap = result["limit"]
            if len(overlap) < required_overlap:
                errors.append(
                    f"{label}: only {len(overlap)}/{result['limit']} ASINs overlap "
                    f"a fresh live search; required {required_overlap}"
                )
            for asin in sorted(overlap):
                if observed_by_asin[asin] != reference_by_asin[asin]:
                    errors.append(f"{label}: title mismatch for shared ASIN {asin}")

            query_tokens = [
                token.casefold()
                for token in re.findall(r"[A-Za-z0-9]+", result["query"])
                if len(token) >= 3
            ]
            titles = [item["title"].casefold() for item in result["products"]]
            any_token = sum(any(token in title for token in query_tokens) for title in titles)
            all_tokens = sum(all(token in title for token in query_tokens) for title in titles)
            comparisons[label] = {
                "fresh_asin_overlap": len(overlap),
                "returned": result["limit"],
                "required_overlap": required_overlap,
                "titles_with_any_query_term": any_token,
                "titles_with_all_query_terms": all_tokens,
            }
            if not query_tokens or any_token < math.ceil(0.8 * result["limit"]):
                errors.append(f"{label}: insufficient visible query relevance")
            if all_tokens < math.ceil(0.6 * result["limit"]):
                errors.append(f"{label}: too few titles contain every query term")
        if default["query"].casefold() == held["query"].casefold():
            errors.append("held case did not change query")
        default_asins = {item["asin"] for item in default["products"]}
        held_asins = {item["asin"] for item in held["products"]}
        if default_asins == held_asins:
            errors.append("changing query did not change the product set")
    return {
        "status": "pass" if not errors else "fail",
        "verification_version": VERIFICATION_VERSION,
        "errors": errors,
        "cases": {
            "default": {"query": default.get("query"), "returned": len(default.get("products", []))},
            "held": {"query": held.get("query"), "returned": len(held.get("products", []))},
        },
        "fresh_live_reference": live,
        "comparisons": comparisons,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--default-dir", type=Path, required=True)
    parser.add_argument("--held-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(verify(inside_shared(args.default_dir), inside_shared(args.held_dir)))
    output = inside_shared(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
