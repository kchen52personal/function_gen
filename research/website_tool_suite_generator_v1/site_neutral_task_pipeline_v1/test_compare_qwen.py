import json
import sys

import api_timing
import compare_qwen
import factory

def test_api_timing_keeps_main_and_inner_separate(tmp_path):
    main = tmp_path/'qwen'/'hans_website_tool_webwright_2026-11.jsonl'
    main.parent.mkdir()
    main.write_text(json.dumps({'duration_ms':1000,'success':True,'prompt_tokens':3,'completion_tokens':2})+'\n')
    inner = main.parent/'hans_webwright_inner_tool_2026-11.jsonl'
    inner.write_text(json.dumps({'duration_ms':20000,'success':False,'prompt_tokens':0,'completion_tokens':0})+'\n')
    value = api_timing.summarize(tmp_path)
    assert value['main_agent']['median_call_seconds']==1
    assert value['inner_tools']['median_call_seconds']==20
    assert value['inner_tools']['failed_logged_calls']==1
    assert 'overlap' in value['limitation']

def test_paired_comparison_uses_same_tasks_and_timeout_without_held(tmp_path, monkeypatch):
    source = tmp_path/'source'
    source.mkdir()
    url='https://example.org/'
    proposal={'contract':{'goal':'Search a catalog','boundary':'Read-only','success_contract':'Return a title',
        'task_template':'Find {query}', 'arguments':[{'name':'query','type':'string','description':'Search'}],
        'returns':{'title':'str'}}, 'cases':[{'query':f'item{x:02d}'} for x in range(12)]}
    _, split = factory.create_taskset(url,'Search the catalog',{'start_url':url,'allowed_host':'example.org',
        'pages':[]},source/'tasks',requester=lambda *a:json.dumps(proposal))
    (source/'summary.json').write_text(json.dumps({'website':url,'split_hashes':split['manifest_sha256']}))
    calls=[]
    def development(site,contract,tasks,out,**kwargs):
        calls.append((tasks[0]['task_id'],kwargs['model'],kwargs['attempt_timeout'],kwargs['queue_timeout']))
        script=out/tasks[0]['task_id']/kwargs['model']/'agent/final_script.py'
        script.parent.mkdir(parents=True)
        script.write_text('# submitted')
        (out/'summary.json').write_text(json.dumps({'attempts':[{'task_id':tasks[0]['task_id'],
          'trajectory_status':'Submitted','process_exit':0,'script_sha256':compare_qwen.digest(script)}]}))
    monkeypatch.setattr(compare_qwen.development,'run',development)
    monkeypatch.setattr(compare_qwen,'evaluate_case',lambda *a,**kw:{'status':'inconclusive'})
    out=tmp_path/'comparison'
    monkeypatch.setattr(sys,'argv',['compare_qwen.py',str(source),str(out)])
    compare_qwen.main()
    result=json.loads((out/'summary.json').read_text())
    assert result['status']=='complete_exploratory' and result['held_out_opened'] is False
    assert len(calls)==4 and all(x[2:]==(1800,1840) for x in calls)
    assert calls[0][0]==calls[1][0] and calls[2][0]==calls[3][0] and calls[0][0]!=calls[2][0]
    assert [x[1] for x in calls]==['qwen3.8-flash','qwen3.5-plus','qwen3.5-plus','qwen3.8-flash']
