import asyncio
import argparse
import json
from pathlib import Path
from playwright.async_api import async_playwright


async def site_function(request: str, output_dir: str) -> dict:
    """
    Create a new note in Youdao Cloud Notes based on user request.
    
    Args:
        request: User's natural language request specifying note type
        output_dir: Directory for screenshots and logs
    
    Returns:
        dict with editor_url, selected_type, status_message
    """
    output_path = Path(output_dir)
    screenshots_dir = output_path / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    log_file = output_path / "final_script_log.txt"
    
    log_lines = []
    
    def log(step, action):
        log_lines.append(f"step {step} action: {action}")
    
    # Supported note types and their keywords
    note_type_mapping = {
        "空白文档": ["空白文档", "空白", "文档", "default", "普通笔记"],
        "Markdown": ["Markdown", "markdown", "md"],
        "脑图": ["脑图", "思维导图", "mindmap", "mind map"],
        "流程图": ["流程图", "flowchart", "flow", "drawio"]
    }
    
    # Parse request to determine note type
    selected_type = "空白文档"  # default
    request_lower = request.lower()
    type_found = False
    
    for note_type, keywords in note_type_mapping.items():
        for keyword in keywords:
            if keyword.lower() in request_lower or keyword in request:
                selected_type = note_type
                type_found = True
                break
        if type_found:
            break
    
    log(0, f"params: request={request}")
    
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 1800})
        page = await context.new_page()
        
        # Navigate to Youdao Cloud Notes
        await page.goto("https://note.youdao.com/web/", wait_until="domcontentloaded")
        await asyncio.sleep(3)
        log(1, "Navigated to https://note.youdao.com/web/")
        
        # Capture before screenshot showing initial page state
        await page.screenshot(path=str(screenshots_dir / "before.png"))
        log(2, "Captured before.png screenshot showing initial page state")
        
        # Click the '新建' (create new) button
        try:
            # Try multiple selector strategies for robustness
            new_btn = None
            selectors = [
                ".operate-btn-text.operate-create-text",
                ".operate-create-text",
                ".operate-create",
                "text=新建",
                "[class*='create']"
            ]
            
            for selector in selectors:
                try:
                    new_btn = page.locator(selector).first
                    if await new_btn.count() > 0:
                        await new_btn.click()
                        break
                except:
                    continue
            
            if new_btn is None:
                raise Exception("Could not find create button")
            
            await asyncio.sleep(1.5)
            log(3, "Clicked '新建' button to open creation menu")
        except Exception as e:
            await browser.close()
            result = {
                "editor_url": "",
                "selected_type": selected_type,
                "status_message": f"无法找到新建按钮，站点可能受限：{str(e)}"
            }
            final_result = {
                "parameters": {"request": request},
                "result": result,
                "source_url": "https://note.youdao.com/web/"
            }
            with open(log_file, 'w', encoding='utf-8') as f:
                for line in log_lines:
                    f.write(line + '\n')
                f.write(f"final result: {json.dumps(final_result, ensure_ascii=False)}\n")
            return result
        
        # Select the note type from the menu
        try:
            # Try multiple strategies to find and click the type option
            type_option = None
            type_selectors = [
                page.get_by_text(selected_type).first,
                page.locator(f"text={selected_type}").first,
                page.locator(f"span:has-text('{selected_type}')").first,
                page.locator(f"li:has-text('{selected_type}')").first,
            ]
            
            for selector in type_selectors:
                try:
                    if await selector.count() > 0:
                        await selector.click(force=True)
                        type_option = selector
                        break
                except:
                    continue
            
            if type_option is None:
                raise Exception(f"Type '{selected_type}' not found in menu")
            
            await asyncio.sleep(3)
            log(4, f"Selected '{selected_type}' from creation menu")
        except Exception as e:
            await browser.close()
            result = {
                "editor_url": "",
                "selected_type": selected_type,
                "status_message": f"指定的类型 '{selected_type}' 不存在或不明确，无法创建"
            }
            final_result = {
                "parameters": {"request": request},
                "result": result,
                "source_url": "https://note.youdao.com/web/"
            }
            with open(log_file, 'w', encoding='utf-8') as f:
                for line in log_lines:
                    f.write(line + '\n')
                f.write(f"final result: {json.dumps(final_result, ensure_ascii=False)}\n")
            return result
        
        # Get the editor URL
        editor_url = page.url
        log(5, f"Editor opened with URL: {editor_url}")
        
        # Capture after screenshot showing the opened editor
        await page.screenshot(path=str(screenshots_dir / "after.png"))
        log(6, "Captured after.png screenshot showing opened editor")
        
        await browser.close()
        
        result = {
            "editor_url": editor_url,
            "selected_type": selected_type,
            "status_message": f"成功创建{selected_type}编辑器"
        }
        
        final_result = {
            "parameters": {"request": request},
            "result": result,
            "source_url": "https://note.youdao.com/web/"
        }
        
        with open(log_file, 'w', encoding='utf-8') as f:
            for line in log_lines:
                f.write(line + '\n')
            f.write(f"final result: {json.dumps(final_result, ensure_ascii=False)}\n")
        
        return result


def main():
    parser = argparse.ArgumentParser(description='Create a note in Youdao Cloud Notes')
    parser.add_argument('--request', type=str, required=True, help='User request for note type')
    parser.add_argument('--output-dir', type=str, required=True, help='Output directory for screenshots and logs')
    args = parser.parse_args()
    
    result = asyncio.run(site_function(args.request, args.output_dir))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
