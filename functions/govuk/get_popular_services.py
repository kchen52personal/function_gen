import asyncio
import argparse
import json
import os
import sys
from pathlib import Path
from playwright.async_api import async_playwright
from urllib.parse import urljoin

async def main():
    parser = argparse.ArgumentParser(description='Get popular services from GOV.UK')
    parser.add_argument('--limit', type=int, default=5, help='Number of services to retrieve (1-6)')
    parser.add_argument('--output-dir', type=str, required=True, help='Output directory')
    args = parser.parse_args()
    
    # Validate limit
    if args.limit < 1 or args.limit > 6:
        print(f"Error: limit must be between 1 and 6, got {args.limit}", file=sys.stderr)
        sys.exit(1)
    
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshots_dir = output_dir / 'screenshots'
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    
    log_path = output_dir / 'final_script_log.txt'
    log_lines = []
    log_lines.append(f"step 0 action: params: limit={args.limit}, output-dir={args.output_dir}")
    
    base_url = 'https://www.gov.uk/'
    result_items = []
    final_url = base_url
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # Navigate to homepage
        await page.goto(base_url, wait_until='domcontentloaded')
        final_url = page.url
        
        # Take before screenshot
        await page.screenshot(path=str(screenshots_dir / 'before.png'))
        
        # Wait for the popular services section to be visible
        await page.wait_for_selector('section.homepage-section__popular-links', state='visible', timeout=10000)
        
        # Find the Popular on GOV.UK section
        popular_section = page.locator('section.homepage-section__popular-links')
        
        # Find the list within the section
        services_list = popular_section.locator('ul.homepage-most-viewed-list')
        
        # Get all service links
        link_elements = services_list.locator('li.homepage-most-viewed-list__item a.govuk-link')
        count = await link_elements.count()
        
        # Extract up to limit services
        extract_count = min(args.limit, count)
        for i in range(extract_count):
            link = link_elements.nth(i)
            title = await link.inner_text()
            href = await link.get_attribute('href')
            
            # Collapse whitespace and trim
            title = ' '.join(title.split())
            
            # Resolve URL
            full_url = urljoin(base_url, href) if href else base_url
            
            result_items.append({
                'title': title,
                'url': full_url
            })
        
        # Take after screenshot
        await page.screenshot(path=str(screenshots_dir / 'after.png'))
        
        await browser.close()
    
    # Build output
    output = {
        'parameters': {
            'limit': args.limit
        },
        'result': {
            'items': result_items
        },
        'source_url': final_url
    }
    
    # Write log
    log_lines.append(f"final result: {json.dumps(output)}")
    with open(log_path, 'w') as f:
        f.write('\n'.join(log_lines))
    
    # Print JSON to stdout
    print(json.dumps(output))

if __name__ == '__main__':
    asyncio.run(main())
