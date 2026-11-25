import asyncio
import subprocess

import codex_dom_evidence as dom


def test_dom_evidence_rejects_foreign_and_missing_sources():
    assert asyncio.run(dom.capture('https://site.test/', {}))['status'] == 'unavailable'
    candidate = {'proposal_evidence': {'resolved': {
        'page_url': 'https://elsewhere.test/', 'record': {'tag': 'h2', 'text': 'Topics'}}}}
    result = asyncio.run(dom.capture('https://site.test/', candidate))
    assert result['status'] == 'unavailable'
    assert 'first-party' in result['reason']


def test_dom_evidence_requests_cited_tag_and_attributes(monkeypatch):
    class Locator:
        async def evaluate_all(self, code, attributes):
            assert attributes['text'] == 'Topics'
            return [{'element': '<h2>Topics</h2>', 'section': '<section>Topics</section>'}]
    class Page:
        url = 'https://site.test/topics'
        async def goto(self, url, **kwargs):
            assert url == self.url
            return type('Response', (), {'status': 200})()
        def locator(self, tag):
            assert tag == 'h2'
            return Locator()
    class Browser:
        async def new_page(self, **kwargs):
            return Page()
        async def close(self):
            pass
    class Chromium:
        async def launch(self, **kwargs):
            return Browser()
    class Context:
        async def __aenter__(self):
            return type('Playwright', (), {'chromium': Chromium()})()
        async def __aexit__(self, *args):
            pass
    monkeypatch.setattr(dom, 'async_playwright', Context)
    candidate = {'proposal_evidence': {'resolved': {
        'page_url': 'https://site.test/topics', 'record': {'tag': 'h2', 'text': 'Topics'}}}}
    result = asyncio.run(dom.capture('https://site.test/', candidate))
    assert result['status'] == 'captured'
    assert '<section>' in result['matches'][0]['section']


def test_dynamic_title_fallback_requires_unique_name_and_accessible_label():
    program = 'const capture = (' + dom.DOM_CAPTURE_JS + r''');
      const element = (name, label, title) => ({innerText: '', outerHTML: '<input>',
        parentElement: null, nextElementSibling: null, closest: () => null,
        getAttribute: key => ({name, 'aria-label': label, title}[key] || '')});
      const attrs = {name: 'search', aria_label: 'Search', title: 'Search [Alt+Shift+f]'};
      const good = capture([element('search', 'Search', 'Search [f]'),
                            element('search', '', 'Other')], attrs);
      if (good.length !== 1 || good[0].match_method !== 'unique_name_and_aria_label_without_title')
        throw Error('unique fallback did not match');
      const ambiguous = [element('search', 'Search', 'A'), element('search', 'Search', 'B')];
      if (capture(ambiguous, attrs).length) throw Error('ambiguous match accepted');
      attrs.aria_label = '';
      if (capture(ambiguous, attrs).length) throw Error('missing accessible label accepted');
    '''
    subprocess.run(['node', '-e', program], check=True, capture_output=True, text=True)


def test_form_summary_omits_values_and_action_query():
    program = 'const capture = (' + dom.DOM_CAPTURE_JS + r''');
      const attrsFor = values => key => values[key] || '';
      const controls = [
        {tagName: 'INPUT', innerText: '', getAttribute: attrsFor({name: 'search', type: 'search', value: 'SECRET'})},
        {tagName: 'INPUT', innerText: '', getAttribute: attrsFor({name: 'csrf', type: 'hidden', value: 'SECRET'})},
        {tagName: 'BUTTON', innerText: 'Search', getAttribute: attrsFor({type: 'submit'})}];
      const form = {action: 'https://site.test/search?token=SECRET', method: 'get',
                    querySelectorAll: () => controls};
      const e = {innerText: '', outerHTML: '<input>', parentElement: null, nextElementSibling: null,
        closest: tag => tag === 'form' ? form : null,
        getAttribute: attrsFor({name: 'search', 'aria-label': 'Search'})};
      const summary = capture([e], {name: 'search', aria_label: 'Search'})[0].form_summary;
      if (summary.action_without_query !== 'https://site.test/search') throw Error('bad action');
      if (summary.controls.length !== 3 || summary.controls[2].text !== 'Search') throw Error('controls missing');
      if (JSON.stringify(summary).includes('SECRET')) throw Error('sensitive value leaked');
    '''
    subprocess.run(['node', '-e', program], check=True, capture_output=True, text=True)


def test_section_context_uses_bounded_nearest_label():
    program = 'const capture = (' + dom.DOM_CAPTURE_JS + r''');
      const heading = {innerText: 'Browse by subject', textContent: 'Browse by subject'};
      const section = {outerHTML:'<section>', getAttribute: key => key === 'aria-labelledby' ? 'heading-id' : '',
                       querySelector: () => null};
      const e = {innerText: 'Culture', outerHTML:'<li>Culture</li>', parentElement:null,
                 nextElementSibling:null, closest: tag => tag === 'section' ? section : null,
                 ownerDocument:{getElementById: id => id === 'heading-id' ? heading : null},
                 getAttribute: () => ''};
      const result = capture([e], {text:'Culture'})[0];
      if (result.section_context.heading !== 'Browse by subject') throw Error('wrong section context');
      if (!result.section_context.note.includes('do not establish task scope')) throw Error('missing caveat');
    '''
    subprocess.run(['node', '-e', program], check=True, capture_output=True, text=True)


def test_section_context_keeps_ancestor_heading_chain():
    program = 'const capture = (' + dom.DOM_CAPTURE_JS + r''');
      const headings = {inner:{innerText:'Culture and the arts'}, outer:{innerText:'Browse by subject'}};
      const outer = {outerHTML:'<section>', parentElement:null,
                     getAttribute:key=>key==='aria-labelledby'?'outer':'', querySelector:()=>null};
      const inner = {outerHTML:'<section>', parentElement:{closest:()=>outer},
                     getAttribute:key=>key==='aria-labelledby'?'inner':'', querySelector:()=>null};
      const e = {innerText:'Culture and the arts', outerHTML:'<h3>Culture and the arts</h3>',
                 parentElement:null, nextElementSibling:null, closest:tag=>tag==='section'?inner:null,
                 ownerDocument:{getElementById:id=>headings[id]}, getAttribute:()=>''};
      const c = capture([e], {text:'Culture and the arts'})[0].section_context;
      if (c.heading !== 'Culture and the arts' || c.ancestor_headings.join('|') !==
          'Culture and the arts|Browse by subject') throw Error('wrong ancestor chain');
    '''
    subprocess.run(['node', '-e', program], check=True, capture_output=True, text=True)


def test_return_context_is_bounded_and_advisory(monkeypatch):
    calls = []
    async def fake_capture(start_url, candidate):
        calls.append(candidate['proposal_evidence']['evidence_id'])
        return {'status': 'captured', 'source_url': start_url,
                'matches': [{'element': 'x' * 1000, 'parent': 'SECRET_PARENT',
                             'section_context': {'ancestor_headings': ['Section']}}]}
    monkeypatch.setattr(dom, 'capture', fake_capture)
    candidate = {'proposal_return_evidence': {
        f'field_{i}': {'evidence_id': str(i), 'resolved': {}} for i in range(4)}}
    result = asyncio.run(dom.capture_return_context('https://site.test/', candidate))
    assert result['status'] == 'captured' and result['truncated'] is True
    assert len(calls) == len(result['fields']) == 3
    assert len(result['fields']['field_0']['matches'][0]['element']) == 500
    assert 'SECRET_PARENT' not in str(result)
    assert 'not a scope proof' in result['limitation']
