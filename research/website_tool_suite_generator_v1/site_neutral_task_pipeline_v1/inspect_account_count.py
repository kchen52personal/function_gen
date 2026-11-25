"""Read-only Youdao UI count diagnostic; emits structure/counts, not note contents."""
import json
import hashlib
import os
from playwright.sync_api import sync_playwright

os.environ['PLAYWRIGHT_BROWSERS_PATH']='/shared/hans/research/website_tool_suite_generator_v1/.playwright-browsers'
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    context=browser.new_context(storage_state='/shared/hans/.secrets/browser_sessions/desktop_545ba418046b/storage_state.json',viewport={'width':1280,'height':900})
    page=context.new_page()
    page.goto('https://note.youdao.com/web/',wait_until='domcontentloaded',timeout=60000)
    page.wait_for_timeout(12000)
    result=page.evaluate('''() => {
      const els=[...document.querySelectorAll('*')];
      const counters={};
      for (const el of els) {
        for (const c of (typeof el.className==='string' ? el.className.split(/\\s+/) : [])) {
          if (c && /(?:note|file|list|item)/i.test(c)) counters[c]=(counters[c]||0)+1;
        }
      }
      const scroll=els.filter(e=>e.scrollHeight>e.clientHeight+50 && e.clientHeight>100)
        .map(e=>({tag:e.tagName,classes:typeof e.className==='string'?e.className:'',height:e.clientHeight,scrollHeight:e.scrollHeight,children:e.children.length})).slice(0,15);
      const sample=document.querySelector('.file-item');
      const attr_names=sample ? [...sample.querySelectorAll('*'),sample].flatMap(e=>[...e.attributes].map(a=>a.name)) : [];
      return {url:location.origin+location.pathname, title:document.title,
        item_attribute_names:[...new Set(attr_names)],
        class_counts:Object.fromEntries(Object.entries(counters).sort((a,b)=>b[1]-a[1]).slice(0,35)), scroll};
    }''')
    # The UI loads a page at a time. Scroll the list without opening a note;
    # keep only one-way fingerprints of visible row text in process memory.
    fingerprints=set()
    history=[]
    stagnant=0
    for index in range(120):
        rows=page.locator('.file-item').all_inner_texts()
        for row in rows:
            fingerprints.add(hashlib.sha256(row.encode('utf-8')).hexdigest())
        position=page.evaluate('''() => {
            const e=document.querySelector('.list-bd');
            if (!e) return null;
            e.scrollTop=e.scrollHeight;
            return {top:e.scrollTop,height:e.scrollHeight,client:e.clientHeight};
        }''')
        page.wait_for_timeout(1500)
        state=(len(fingerprints),position['height'] if position else None)
        stagnant=stagnant+1 if history and history[-1]==state else 0
        history.append(state)
        if stagnant>=4: break
    result.update(visible_row_fingerprints=len(fingerprints), iterations=len(history),
                  final_scroll=position, stable_iterations=stagnant,
                  limitation='Distinct visible row-text fingerprints, not authoritative file IDs; duplicates, virtualized rendering, folders and concurrent changes may affect count.')
    print(json.dumps(result,ensure_ascii=False))
    browser.close()
