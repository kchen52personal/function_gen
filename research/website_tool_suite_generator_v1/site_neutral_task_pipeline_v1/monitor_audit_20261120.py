"""Bounded one-hour, local-only metadata monitor for active Youdao experiments.

Reads run summaries, event metadata and diagnostic categories; never reads held
requests, user note text, credentials, model responses or screenshots.
"""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import time

BASE=Path('/shared/hans/research/website_tool_suite_generator_v1/automated_runs')
OUT=BASE/'youdao_pipeline_audit_monitor_20261120_v1'
NAMES=('youdao_new_item_7dev3held_20261120_static_v1',
       'youdao_new_item_zh_7dev3held_20261120_static_v1',
       'youdao_zh_qwen38_two_dev_20261120_v1')


def read(path):
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return {}


def snapshot():
    result={'utc':datetime.now(timezone.utc).isoformat(),'runs':{}}
    for name in NAMES:
        root=BASE/name
        summary=read(root/'summary.json')
        events=[]
        try:
            for line in (root/'events.jsonl').read_text().splitlines():
                try:events.append(json.loads(line))
                except ValueError:pass
        except OSError:pass
        finished=[x for x in events if x.get('event')=='attempt_finished']
        started=[x for x in events if x.get('event')=='attempt_started']
        signals=Counter()
        for path in root.glob('development_round*/round_*/*/*/failure_diagnostics.json'):
            for key,value in read(path).get('signal_counts',{}).items():
                if isinstance(value,int):signals[key]+=value
        result['runs'][name]={'status':summary.get('status'),'held_opened':summary.get('held_opened'),
            'attempts_finished':len(finished),
            'submitted':sum(x.get('trajectory_status')=='Submitted' for x in finished),
            'last_started_task_id':started[-1].get('task_id') if started else None,
            'last_started_utc':started[-1].get('utc') if started else None,
            'finished_exit_codes':dict(Counter(str(x.get('process_exit')) for x in finished)),
            'diagnostic_signals':dict(signals)}
    count=read(BASE/'youdao_static_count_watch_20261120_v1/summary.json')
    result['count_monitor']={k:count.get(k) for k in ('status','baseline_distinct_visible_row_texts','after_distinct_visible_row_texts','approximate_difference')}
    return result


def main():
    OUT.mkdir(mode=0o700,exist_ok=False)
    started=time.monotonic()
    deadline=started+3600
    while True:
        data=snapshot()
        data['monitor_status']='complete' if time.monotonic()>=deadline else 'monitoring'
        (OUT/'summary.json').write_text(json.dumps(data,indent=2)+'\n')
        with (OUT/'history.jsonl').open('a') as f:f.write(json.dumps(data)+'\n')
        if time.monotonic()>=deadline:break
        time.sleep(min(300,max(0,deadline-time.monotonic())))


if __name__=='__main__':main()
