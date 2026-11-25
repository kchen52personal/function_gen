#!/usr/bin/env python3
"""Run bounded one-page access probes for the frozen cross-site registry."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from reconnaissance import reconnaissance


async def probe(
    site: dict[str, Any],
    output_root: Path,
    semaphore: asyncio.Semaphore,
    max_pages: int,
) -> dict[str, Any]:
    url = site.get("start_url") or site.get("official_reference_url")
    if not url:
        return {
            "site_id": site["site_id"],
            "label": site["label"],
            "status": "unresolved_missing_url",
        }
    async with semaphore:
        output = output_root / site["site_id"] / "recon"
        result = await reconnaissance(url, output, max_pages=max_pages)
    page = result["pages"][0] if result.get("pages") else {}
    frames = page.get("frames", [])
    surface_counts = {
        key: sum(len(frame.get(key, [])) for frame in frames)
        for key in ("headings", "links", "actions", "fields", "forms", "content")
    }
    probe_error = page.get("probe_error")
    status = "reachable" if not probe_error and page.get("http_status") else "access_failure"
    return {
        "site_id": site["site_id"],
        "label": site["label"],
        "input_url": url,
        "url_kind": "start_url" if site.get("start_url") else "reference_only",
        "status": status,
        "http_status": page.get("http_status"),
        "final_url": page.get("final_url"),
        "title": page.get("title"),
        "probe_error": probe_error,
        "error_message": page.get("error_message"),
        "surface_counts": surface_counts,
        "pages_attempted": result.get("pages_attempted"),
        "recon": str(output / "recon.json"),
    }


async def run(
    registry: Path,
    output_root: Path,
    concurrency: int,
    max_pages: int,
    site_ids: set[str],
) -> dict[str, Any]:
    sites = json.loads(registry.read_text(encoding="utf-8"))["sites"]
    sites = [site for site in sites if site["site_id"] != "netease_cloud_music"]
    if site_ids:
        sites = [site for site in sites if site["site_id"] in site_ids]
    semaphore = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(*(
        probe(site, output_root, semaphore, max_pages) for site in sites
    ))
    summary = {
        "policy": "one_page_read_only_access_probe; reference URLs are not promoted to functional start URLs",
        "concurrency": concurrency,
        "max_pages": max_pages,
        "results": results,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "access_probe_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("registry", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--site", action="append", default=[])
    args = parser.parse_args()
    if not 1 <= args.concurrency <= 3:
        raise ValueError("concurrency must be between 1 and 3")
    if not 1 <= args.max_pages <= 12:
        raise ValueError("max-pages must be between 1 and 12")
    result = asyncio.run(run(
        args.registry,
        args.output_root,
        args.concurrency,
        args.max_pages,
        set(args.site),
    ))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
