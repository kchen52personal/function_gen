#!/usr/bin/env python3
"""Run manually selected existing tools and verify them against fresh live pages."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse

from playwright.async_api import async_playwright


SHARED_ROOT = Path("/shared/hans").resolve()
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def inside_shared(path: Path) -> Path:
    resolved = path.resolve()
    if resolved != SHARED_ROOT and SHARED_ROOT not in resolved.parents:
        raise ValueError("all paths must stay inside /shared/hans")
    return resolved


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_final_result(log_path: Path) -> dict[str, Any]:
    if not log_path.is_file():
        raise ValueError(f"missing tool log: {log_path}")
    for line in reversed(log_path.read_text(encoding="utf-8").splitlines()):
        if line.startswith("final result: "):
            value = json.loads(line[len("final result: "):])
            if not isinstance(value, dict):
                raise ValueError("final result is not a JSON object")
            return value
    raise ValueError(f"missing final result JSON: {log_path}")


def run_command(case: dict[str, Any], run_dir: Path, timeout_seconds: int) -> dict[str, Any]:
    source = inside_shared(Path(case["source_script"]))
    if not source.is_file():
        raise ValueError(f"source tool does not exist: {source}")
    run_dir.mkdir(parents=True, exist_ok=False)
    script = run_dir / "final_script.py"
    shutil.copy2(source, script)
    arguments = case["arguments"]
    command = ["python", str(script)]
    log_dir = run_dir
    if case["tool"] == "search_products":
        log_dir = run_dir / "output"
        command.extend([
            "--query", str(arguments["query"]),
            "--limit", str(arguments["limit"]),
            "--output-dir", str(log_dir),
        ])
    elif case["tool"] == "search_articles":
        command.extend([
            "--query", str(arguments["query"]),
            "--limit", str(arguments["limit"]),
        ])
    elif case["tool"] == "get_chart":
        command.extend([
            "--chart-name", str(arguments["chart_name"]),
            "--limit", str(arguments["limit"]),
        ])
    else:
        raise ValueError(f"unsupported manual tool: {case['tool']}")

    started = datetime.now(timezone.utc)
    completed = subprocess.run(
        command,
        cwd=run_dir,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
        env={
            **__import__("os").environ,
            "PLAYWRIGHT_BROWSERS_PATH": str(PROJECT_ROOT / ".playwright-browsers"),
        },
    )
    finished = datetime.now(timezone.utc)
    (run_dir / "stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (run_dir / "stderr.txt").write_text(completed.stderr, encoding="utf-8")
    result = read_final_result(log_dir / "final_script_log.txt")
    return {
        "command": command,
        "exit_code": completed.returncode,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "log_dir": str(log_dir),
        "result": result,
    }


async def amazon_reference(query: str, limit: int) -> list[dict[str, str]]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 1800})
        page = await context.new_page()
        await page.goto("https://www.amazon.sg/", wait_until="domcontentloaded", timeout=60_000)
        search = page.locator("input#twotabsearchtextbox")
        await search.fill(query)
        await search.press("Enter")
        rows = page.locator('div[data-component-type="s-search-result"][data-asin]')
        await rows.first.wait_for(state="visible", timeout=30_000)
        records: list[dict[str, str]] = []
        for index in range(await rows.count()):
            row = rows.nth(index)
            asin = await row.get_attribute("data-asin")
            title_node = row.locator('div[data-cy="title-recipe"] h2').first
            if not asin or await title_node.count() == 0:
                continue
            title = (await title_node.inner_text()).strip()
            if title:
                records.append({"asin": asin, "title": title})
            if len(records) == limit:
                break
        await context.close()
        await browser.close()
    return records


async def verify_amazon(case: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    result = execution["result"]
    products = result.get("products", [])
    limit = case["arguments"]["limit"]
    errors: list[str] = []
    if execution["exit_code"] != 0:
        errors.append(f"tool exited {execution['exit_code']}")
    if len(products) != limit:
        errors.append(f"returned {len(products)} products, expected {limit}")
    reference = await amazon_reference(case["arguments"]["query"], limit)
    observed = {item.get("asin"): item.get("title") for item in products}
    expected = {item["asin"]: item["title"] for item in reference}
    overlap = set(observed) & set(expected)
    required = math.ceil(0.75 * limit)
    if len(overlap) < required:
        errors.append(f"fresh live ASIN overlap {len(overlap)}/{limit}; required {required}")
    for asin in overlap:
        if " ".join(str(observed[asin]).split()) != " ".join(expected[asin].split()):
            errors.append(f"title mismatch for ASIN {asin}")
    return {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "fresh_reference": reference,
        "comparison": {"overlap": len(overlap), "required": required},
    }


async def wikipedia_reference(query: str, limit: int) -> list[dict[str, str]]:
    url = (
        "https://en.wikipedia.org/w/index.php?title=Special%3ASearch&search="
        f"{quote_plus(query)}&ns0=1&fulltext=1"
    )
    async with async_playwright() as playwright:
        browser = await playwright.firefox.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 1800})
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        rows = page.locator("ul.mw-search-results > li")
        await rows.first.wait_for(state="visible", timeout=30_000)
        records: list[dict[str, str]] = []
        for index in range(await rows.count()):
            row = rows.nth(index)
            link = row.locator("div.mw-search-result-heading > a").first
            summary = row.locator("div.searchresult").first
            if await link.count() == 0 or await summary.count() == 0:
                continue
            title = (await link.inner_text()).strip()
            href = await link.get_attribute("href")
            snippet = " ".join((await summary.inner_text()).split())
            if title and href and snippet:
                records.append({
                    "title": title,
                    "snippet": snippet,
                    "url": urljoin("https://en.wikipedia.org", href),
                })
            if len(records) == limit:
                break
        await context.close()
        await browser.close()
    return records


async def verify_wikipedia(case: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    observed_rows = execution["result"].get("results", [])
    limit = case["arguments"]["limit"]
    reference = await wikipedia_reference(case["arguments"]["query"], limit)
    errors: list[str] = []
    if execution["exit_code"] != 0:
        errors.append(f"tool exited {execution['exit_code']}")
    if len(observed_rows) != limit:
        errors.append(f"returned {len(observed_rows)} rows, expected {limit}")
    observed = {row.get("url"): row.get("title") for row in observed_rows}
    expected = {row["url"]: row["title"] for row in reference}
    overlap = set(observed) & set(expected)
    required = math.ceil(0.8 * limit)
    if len(overlap) < required:
        errors.append(f"fresh live URL overlap {len(overlap)}/{limit}; required {required}")
    for url in overlap:
        if observed[url] != expected[url]:
            errors.append(f"title mismatch for {url}")
    return {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "fresh_reference": reference,
        "comparison": {"overlap": len(overlap), "required": required},
    }


async def netease_reference(chart_name: str, limit: int) -> list[dict[str, Any]]:
    async with async_playwright() as playwright:
        browser = await playwright.firefox.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 1800})
        page = await context.new_page()
        await page.goto("https://music.163.com/", wait_until="domcontentloaded", timeout=60_000)
        await page.get_by_role("link", name="排行榜", exact=True).click()
        await page.wait_for_timeout(1_500)
        frame = page.frame(name="contentFrame")
        if frame is None:
            raise RuntimeError("NetEase contentFrame unavailable")
        links = frame.locator("a")
        indexes = await links.evaluate_all(
            "(els, expected) => els.map((e, i) => "
            "(e.innerText || '').trim() === expected ? i : -1).filter(i => i >= 0)",
            chart_name,
        )
        if len(indexes) != 1:
            raise RuntimeError(f"expected one chart link, found {len(indexes)}")
        await links.nth(indexes[0]).click()
        rows = frame.locator("table tbody tr")
        await rows.first.wait_for(state="visible", timeout=30_000)
        records: list[dict[str, Any]] = []
        for index in range(limit):
            data = await rows.nth(index).evaluate(
                """el => {
                  const rank = el.querySelector('td .num')?.textContent?.trim() || '';
                  const songNode = el.querySelector('td:nth-child(2) b[title]');
                  const songAnchor = songNode?.closest('a');
                  const artistNode = el.querySelector('td:last-child .text[title]');
                  return {rank, song: songNode?.getAttribute('title')?.trim() || '',
                    href: songAnchor?.getAttribute('href') || '',
                    artist: artistNode?.getAttribute('title')?.trim() || ''};
                }"""
            )
            records.append({
                "rank": int(data["rank"]),
                "song": data["song"],
                "artist": data["artist"],
                "url": urljoin("https://music.163.com/", data["href"]),
            })
        await context.close()
        await browser.close()
    return records


async def verify_netease(case: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    observed = execution["result"].get("entries", [])
    arguments = case["arguments"]
    reference = await netease_reference(arguments["chart_name"], arguments["limit"])
    errors: list[str] = []
    if execution["exit_code"] != 0:
        errors.append(f"tool exited {execution['exit_code']}")
    if observed != reference:
        errors.append("tool output does not exactly match a fresh live chart extraction")
    return {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "fresh_reference": reference,
        "comparison": {"exact_match": observed == reference},
    }


async def verify_case(case: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    if case["tool"] == "search_products":
        return await verify_amazon(case, execution)
    if case["tool"] == "search_articles":
        return await verify_wikipedia(case, execution)
    if case["tool"] == "get_chart":
        return await verify_netease(case, execution)
    raise ValueError(f"no independent oracle for {case['tool']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    queue_path = inside_shared(args.queue)
    output_root = inside_shared(args.output_root)
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError(f"output root is not empty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    timeout_seconds = int(queue["policy"]["per_case_timeout_seconds"])
    summary: dict[str, Any] = {
        "status": "running",
        "queue": str(queue_path),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "model_calls": 0,
        "gpu_jobs": 0,
        "cases": [],
    }
    summary_path = output_root / "summary.json"
    write_json(summary_path, summary)
    for case in queue["cases"]:
        record: dict[str, Any] = {
            "case_id": case["case_id"],
            "site_id": case["site_id"],
            "intent": case["intent"],
            "tool": case["tool"],
            "arguments": case["arguments"],
        }
        try:
            execution = run_command(case, output_root / case["case_id"], timeout_seconds)
            verification = asyncio.run(verify_case(case, execution))
            record.update({
                "status": verification["status"],
                "execution": execution,
                "verification": verification,
            })
        except Exception as error:
            record.update({
                "status": "error",
                "error_type": type(error).__name__,
                "error": str(error)[:2000],
            })
        summary["cases"].append(record)
        write_json(summary_path, summary)
    passed = sum(record["status"] == "pass" for record in summary["cases"])
    summary.update({
        "status": "complete",
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "passed": passed,
        "total": len(summary["cases"]),
    })
    write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    raise SystemExit(0 if passed == len(summary["cases"]) else 1)


if __name__ == "__main__":
    main()
