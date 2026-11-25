"""Private fresh-context sign-in check; never logs page content or credentials."""
import asyncio
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse
from playwright.async_api import async_playwright

async def main():
    root=Path(os.environ['WORKSPACE_DIR'])
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        context=await browser.new_context(storage_state=str(root/'private_storage_state.json'))
        page=await context.new_page()
        try:
            response=await page.goto(sys.argv[1],wait_until='domcontentloaded',timeout=60000)
            await page.wait_for_timeout(7000)
            parsed=urlparse(page.url)
            markers={name:False for name in ('新建','新建笔记','新建文档','Markdown','脑图','登录')}
            for frame in page.frames[:8]:
                for name in markers:
                    try:
                        markers[name] = markers[name] or await frame.get_by_text(name,exact=True).first.is_visible(timeout=300)
                    except Exception:
                        pass
            menu={'opened':False,'fixed_type_markers':{}}
            if len(sys.argv)>2 and sys.argv[2]=='inspect-menu':
                for frame in page.frames[:8]:
                    try:
                        control=frame.get_by_text('新建',exact=True).first
                        if await control.is_visible(timeout=500):
                            await control.click(timeout=5000)
                            menu['opened']=True
                            break
                    except Exception:
                        pass
                await page.wait_for_timeout(1000)
                for name in ('笔记','空白文档','文档','Markdown','脑图','思维导图','表格','流程图'):
                    seen=False
                    for frame in page.frames[:8]:
                        try: seen=seen or await frame.get_by_text(name,exact=True).first.is_visible(timeout=300)
                        except Exception: pass
                    menu['fixed_type_markers'][name]=seen
            result={'http_status':response.status if response else None,
                'same_host':parsed.hostname=='note.youdao.com',
                'app_path':parsed.path.startswith('/web/'),
                'sign_in_path':'signin' in parsed.path.lower(),
                'has_note_hash':'/file/' in parsed.fragment,
                'visible_fixed_ui_markers':markers,
                'creation_menu_probe':menu,
                'model_calls':0,'note_content_read':False,
                'limitation':'URL signals alone are not proof that user data can be accessed.'}
            (root/'probe_result.json').write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps(result),flush=True)
        finally:
            await browser.close()
asyncio.run(main())
