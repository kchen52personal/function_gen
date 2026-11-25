import asyncio
import argparse
import json
import os
import re
from pathlib import Path
from playwright.async_api import async_playwright


async def site_function(page, request: str, output_dir: Path) -> dict:
    """
    Search for and open a note in Youdao Cloud Notes based on user request.
    Read-only operation - does not modify any data.
    """
    screenshots_dir = output_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    
    result = {
        "note_title": "",
        "note_type": "",
        "note_url": "",
        "status_message": ""
    }
    
    # Parse the request to extract note name and optional type filter
    note_name = None
    type_filter = None
    
    # Pattern 1: 名为'...' or 名为"..."
    name_match = re.search(r"名为['\"]([^'\"]+)['\"]", request)
    if name_match:
        note_name = name_match.group(1)
    # Pattern 2: 标题为'...' or 标题为"..."
    elif re.search(r"标题为['\"]([^'\"]+)['\"]", request):
        note_name = re.search(r"标题为['\"]([^'\"]+)['\"]", request).group(1)
    # Pattern 3: Look for text after colon
    elif "：" in request or ":" in request:
        match = re.search(r"[：:]\s*['\"]?([^'\"]+)['\"]?", request)
        if match:
            note_name = match.group(1).strip()
    # Pattern 4: Extract from "打开..." or "查找..."
    else:
        for prefix in ["打开", "查找", "搜索", "定位"]:
            if request.startswith(prefix):
                note_name = request[len(prefix):].strip()
                break
    
    # Determine type filter from request
    if ".md" in request or "Markdown" in request or "markdown" in request:
        type_filter = "Markdown"
    elif ".mindmap" in request or "脑图" in request or "思维导图" in request:
        type_filter = "脑图"
    elif ".drawio" in request or "流程图" in request:
        type_filter = "流程图"
    elif "空白文档" in request:
        type_filter = "空白文档"
    
    # Navigate to Youdao Cloud Notes
    await page.goto("https://note.youdao.com/web/", wait_until="domcontentloaded")
    await asyncio.sleep(2)
    
    # Take before screenshot (initial state after navigation)
    await page.screenshot(path=str(screenshots_dir / "before.png"))
    
    if not note_name:
        result["status_message"] = "无法从请求中提取笔记名称"
        return result
    
    # Find and use search functionality
    search_box = None
    search_selectors = [
        'input#file-list-search',
        'input[placeholder*="搜索"]',
        'input[name="search"]',
    ]
    
    for selector in search_selectors:
        locator = page.locator(selector).first
        if await locator.count() > 0:
            search_box = locator
            break
    
    if not search_box:
        search_box = page.get_by_role("textbox", name="搜索笔记").first
        if await search_box.count() == 0:
            search_box = page.get_by_role("textbox", name="搜索笔记 Ctrl+Shift+F").first
    
    if await search_box.count() == 0:
        result["status_message"] = "未找到搜索框"
        return result
    
    # Click to activate search box
    await search_box.click()
    await asyncio.sleep(0.5)
    
    # Remove readonly attribute if present
    try:
        await page.evaluate('''
            const input = document.querySelector('input#file-list-search');
            if (input) {
                input.removeAttribute("readonly");
            }
        ''')
    except:
        pass
    
    # Fill search term
    await search_box.fill(note_name)
    await asyncio.sleep(1)
    await search_box.press("Enter")
    await asyncio.sleep(2)
    
    # Check search results
    aria = await page.locator("body").aria_snapshot()
    
    # Check for empty results
    if "搜索结果为空" in aria or "总共 0 项" in aria:
        result["status_message"] = f"未找到名为'{note_name}'的笔记"
        await page.screenshot(path=str(screenshots_dir / "after.png"))
        return result
    
    # Find matching notes in the list
    list_items = page.get_by_role("listitem")
    item_count = await list_items.count()
    
    if item_count == 0:
        result["status_message"] = f"未找到名为'{note_name}'的笔记"
        await page.screenshot(path=str(screenshots_dir / "after.png"))
        return result
    
    # Look for matching note with type filter
    found_note = None
    found_type = ""
    
    for i in range(item_count):
        item = list_items.nth(i)
        try:
            text = await item.text_content()
            if text and note_name in text:
                # Determine note type from text
                item_type = "空白文档"
                if ".md" in text or "Markdown" in text:
                    item_type = "Markdown"
                elif ".mindmap" in text or "脑图" in text or "思维导图" in text:
                    item_type = "脑图"
                elif ".drawio" in text or "流程图" in text:
                    item_type = "流程图"
                
                # Check type filter
                if type_filter:
                    if item_type == type_filter:
                        found_note = item
                        found_type = item_type
                        break
                else:
                    found_note = item
                    found_type = item_type
                    break
        except:
            continue
    
    if not found_note:
        if type_filter:
            result["status_message"] = f"未找到名为'{note_name}'且类型为{type_filter}的笔记"
        else:
            result["status_message"] = f"未找到名为'{note_name}'的笔记"
        await page.screenshot(path=str(screenshots_dir / "after.png"))
        return result
    
    # Click to open the note
    await found_note.click()
    await asyncio.sleep(3)
    
    # Get current URL
    current_url = page.url
    
    # Extract note title from page if possible
    title_element = page.locator("textbox").first
    page_title = ""
    if await title_element.count() > 0:
        try:
            page_title = await title_element.input_value()
        except:
            page_title = note_name
    else:
        page_title = note_name
    
    # Set result
    result["note_title"] = page_title
    result["note_type"] = found_type
    result["note_url"] = current_url
    result["status_message"] = "成功找到并打开笔记"
    
    # Take after screenshot
    await page.screenshot(path=str(screenshots_dir / "after.png"))
    
    return result


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=str, required=True, 
                        help="Chinese natural language request for finding and opening a note")
    parser.add_argument("--output-dir", type=str, required=True, 
                        help="Output directory for screenshots and log")
    args = parser.parse_args()
    
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    log_file = output_dir / "final_script_log.txt"
    log_lines = []
    
    # Log initial params (step 0)
    log_lines.append(f"step 0 action: params: request={args.request}")
    
    result = None
    source_url = "https://note.youdao.com/web/"
    
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 1800})
        page = await context.new_page()
        
        # Call site_function to perform the actual task
        result = await site_function(page, args.request, output_dir)
        
        if result and result.get("note_url"):
            source_url = result["note_url"]
        
        await browser.close()
    
    # Write final result to log (last line)
    final_result = {
        "parameters": {"request": args.request},
        "result": result,
        "source_url": source_url
    }
    
    log_lines.append(f"final result: {json.dumps(final_result, ensure_ascii=False)}")
    
    with open(log_file, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))
    
    print(json.dumps(final_result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
