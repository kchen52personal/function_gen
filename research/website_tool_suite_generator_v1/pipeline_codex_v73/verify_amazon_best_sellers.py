#!/usr/bin/env python3
"""Independently verify default and held Amazon.sg Best Sellers runs."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import async_playwright


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
    lines = log_path.read_text(encoding="utf-8").splitlines()
    if not lines or not lines[0].startswith("step 0 action: params:"):
        errors.append("first log line does not contain the parameter record")
    if not lines or not lines[-1].startswith("final result: "):
        return {}, errors + ["last log line does not contain final result JSON"]
    try:
        result = json.loads(lines[-1][len("final result: "):])
    except json.JSONDecodeError as error:
        return {}, errors + [f"invalid final result JSON: {error}"]

    screenshot_pairs = (
        (run_dir / "screenshots" / "before.png", run_dir / "screenshots" / "after.png"),
        (run_dir / "before.png", run_dir / "after.png"),
    )
    if not any(before.stat().st_size > 0 and after.stat().st_size > 0
               for before, after in screenshot_pairs
               if before.is_file() and after.is_file()):
        errors.append("missing non-empty before/after screenshot pair")
    return result, errors


def validate_shape(result: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    category = result.get("category")
    limit = result.get("limit")
    products = result.get("products")
    if not isinstance(category, str) or not category:
        errors.append(f"{label}: invalid category")
    if not isinstance(limit, int) or not 1 <= limit <= 20:
        errors.append(f"{label}: invalid limit")
    if not isinstance(products, list) or len(products) != limit:
        errors.append(f"{label}: products length does not equal limit")
        return errors

    asins: list[str] = []
    ranks: list[int] = []
    for index, item in enumerate(products, start=1):
        if not isinstance(item, dict):
            errors.append(f"{label}: product {index} is not an object")
            continue
        asin = item.get("asin")
        title = item.get("title")
        rank = item.get("rank")
        url = item.get("url")
        if not isinstance(asin, str) or not re.fullmatch(r"[A-Z0-9]{10}", asin):
            errors.append(f"{label}: product {index} has invalid ASIN")
        else:
            asins.append(asin)
        if not isinstance(title, str) or not title.strip():
            errors.append(f"{label}: product {index} has empty title")
        if not isinstance(rank, int):
            errors.append(f"{label}: product {index} has invalid rank")
        else:
            ranks.append(rank)
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
    if ranks != list(range(1, len(products) + 1)):
        errors.append(f"{label}: ranks are not ordered 1..limit")
    return errors


async def live_reference(category: str, limit: int) -> list[dict[str, Any]]:
    url = f"https://www.amazon.sg/gp/bestsellers/{category}/"
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 1800})
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        rows = page.locator("div.p13n-desktop-grid div[data-asin]")
        await rows.first.wait_for(state="visible", timeout=30_000)
        reference: list[dict[str, Any]] = []
        for index in range(await rows.count()):
            row = rows.nth(index)
            asin = await row.get_attribute("data-asin")
            badge = row.locator(".zg-bdg-text")
            title_locator = row.locator(
                '.p13n-sc-uncoverable-faceout a[role="link"] '
                'div[class*="p13n-sc-css-line-clamp"]'
            ).first
            if not asin or await badge.count() == 0 or await title_locator.count() == 0:
                continue
            badge_text = (await badge.first.inner_text()).strip()
            match = re.fullmatch(r"#(\d+)", badge_text)
            title = (await title_locator.inner_text()).strip()
            if not match or not title:
                continue
            reference.append({"rank": int(match.group(1)), "asin": asin, "title": title})
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
    if not errors:
        for label, result in (("default", default), ("held", held)):
            reference = await live_reference(result["category"], result["limit"])
            live[label] = reference
            observed = [
                {"rank": item["rank"], "asin": item["asin"], "title": item["title"]}
                for item in result["products"]
            ]
            if observed != reference:
                errors.append(f"{label}: output does not exactly match fresh live ranking")
        if default["category"] == held["category"]:
            errors.append("held case did not change category")
        default_asins = {item["asin"] for item in default["products"]}
        held_asins = {item["asin"] for item in held["products"]}
        if default_asins == held_asins:
            errors.append("changing category did not change the product set")
    return {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "cases": {
            "default": {
                "category": default.get("category"),
                "returned": len(default.get("products", [])),
            },
            "held": {
                "category": held.get("category"),
                "returned": len(held.get("products", [])),
            },
        },
        "fresh_live_reference": live,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--default-dir", type=Path, required=True)
    parser.add_argument("--held-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    default_dir = inside_shared(args.default_dir)
    held_dir = inside_shared(args.held_dir)
    output = inside_shared(args.output)
    result = asyncio.run(verify(default_dir, held_dir))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
