#!/usr/bin/env python3
import argparse
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright


async def site_function(query: str, limit: int, output_dir: Path, screenshots_dir: Path) -> dict:
    """
    Search arXiv for papers matching a specific query and retrieve titles and abstract links.
    This is a read-only function that does not modify site state.
    """
    results = []
    source_url = ""
    
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 1800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()
        
        # Navigate to arXiv advanced search page (setup state)
        await page.goto("https://arxiv.org/search/advanced", wait_until="domcontentloaded")
        await asyncio.sleep(1)
        source_url = page.url
        
        # Capture before screenshot showing the search form
        await page.screenshot(path=str(screenshots_dir / "before.png"))
        
        # Enter search query
        search_term = page.get_by_role("textbox", name="Search term")
        await search_term.fill(query)
        
        # Select "All fields" for search scope
        field_selector = page.get_by_role("combobox", name="Field to search")
        await field_selector.select_option("All fields")
        
        # Submit search
        search_button = page.get_by_role("button", name="Search").first
        await search_button.click()
        
        # Wait for results to load
        await page.wait_for_selector("li.arxiv-result", timeout=30000)
        await asyncio.sleep(2)
        source_url = page.url
        
        # Capture after screenshot showing search results
        await page.screenshot(path=str(screenshots_dir / "after.png"))
        
        # Extract paper titles and abstract links
        result_items = page.locator("li.arxiv-result")
        total_count = await result_items.count()
        papers_to_extract = min(limit, total_count)
        
        for i in range(papers_to_extract):
            item = result_items.nth(i)
            
            # Extract title from .title element
            title_elem = item.locator(".title").first
            title = await title_elem.inner_text()
            title = " ".join(title.split()).strip()
            
            # Extract abstract link (link containing /abs/)
            links = item.locator("a")
            link_count = await links.count()
            abstract_url = ""
            
            for j in range(link_count):
                href = await links.nth(j).get_attribute("href")
                if href and "/abs/" in href:
                    if href.startswith("//"):
                        abstract_url = "https:" + href
                    elif href.startswith("/"):
                        abstract_url = "https://arxiv.org" + href
                    else:
                        abstract_url = href
                    break
            
            if not abstract_url:
                first_link = links.first
                href = await first_link.get_attribute("href")
                if href:
                    if href.startswith("//"):
                        abstract_url = "https:" + href
                    elif href.startswith("/"):
                        abstract_url = "https://arxiv.org" + href
                    else:
                        abstract_url = href
            
            results.append({
                "title": title,
                "abstract_link": abstract_url
            })
        
        await browser.close()
    
    return {
        "results": results,
        "source_url": source_url
    }


def main():
    parser = argparse.ArgumentParser(description="Search arXiv for papers")
    parser.add_argument("--query", type=str, required=True, help="Search query string")
    parser.add_argument("--limit", type=str, required=True, help="Number of results (1-5), as JSON or integer")
    parser.add_argument("--output-dir", type=str, required=True, help="Output directory path")
    args = parser.parse_args()
    
    # Parse query (string, echo as-is)
    query = args.query
    
    # Parse limit (may be JSON string or plain integer)
    try:
        limit_value = json.loads(args.limit)
        if isinstance(limit_value, int):
            limit = limit_value
        else:
            limit = int(limit_value)
    except (json.JSONDecodeError, ValueError, TypeError):
        limit = int(args.limit)
    
    # Validate limit
    if limit < 1 or limit > 5:
        raise ValueError("limit must be an integer between 1 and 5")
    
    # Setup output directories
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshots_dir = output_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    
    # Initialize log file
    log_file = output_dir / "final_script_log.txt"
    log_lines = []
    
    def log_step(step: int, action: str):
        log_lines.append(f"step {step} action: {action}")
    
    # Log initial parameters
    params = {"query": query, "limit": limit}
    log_step(0, f"params: {json.dumps(params)}")
    
    # Execute site_function
    function_result = asyncio.run(site_function(query, limit, output_dir, screenshots_dir))
    
    # Build final result with required structure
    final_result = {
        "parameters": params,
        "result": {"results": function_result["results"]},
        "source_url": function_result["source_url"]
    }
    
    # Log extraction steps
    for i, paper in enumerate(function_result["results"]):
        log_step(i + 1, f"extracted paper {i + 1}: {paper['title'][:50]}...")
    
    # Log final result
    log_lines.append(f"final result: {json.dumps(final_result)}")
    
    # Write log file
    with open(log_file, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n")
    
    # Print final result
    print(json.dumps(final_result, indent=2))


if __name__ == "__main__":
    main()
