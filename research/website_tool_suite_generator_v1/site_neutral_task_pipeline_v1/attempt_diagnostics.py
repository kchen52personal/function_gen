"""Offline failure signals, never a correctness judge or synthesis/selection gate."""
from collections import Counter
import json
from pathlib import Path

PATTERNS={
    'locator_ambiguity':('strict mode violation',),
    'locator_timeout':('locator.click: timeout','locator.fill: timeout','waiting for locator'),
    'navigation_timeout':('page.goto: timeout','navigation timeout'),
    'browser_crash':('target crashed','target page, context or browser has been closed'),
    'network_or_site_access':('err_connection','err_proxy','err_tunnel','destination outside host-selected'),
    'login_or_captcha_text':('login required','扫码登录','captcha'),
    'outer_api_budget':('max_api_calls','api call limit','api-call cap'),
    'inner_tool_budget':('inner-tool model budget exhausted','tool model budget exhausted'),
    'step_limit':('step limit','step_limit'),
    'wall_timeout':('process timed out',),
}


def collect(work,code,info,stderr='',runner_error=None):
    work=Path(work);counts=Counter();events=[];last=None;parse_errors=[]
    paths=sorted((work/'debug/steps').glob('*.json'))
    for path in paths:
        try:
            data=json.loads(path.read_text())
            text=json.dumps(data.get('outputs',[]),ensure_ascii=False).casefold()
            hits=[name for name,phrases in PATTERNS.items() if any(p in text for p in phrases)]
            counts.update(hits)
            command=data.get('command_text') or data.get('bash_command') or data.get('python_code') or ''
            last={'step':data.get('step'),'file':str(path.relative_to(work)),
                  'command_excerpt':command[:2000]}
            if hits:events.append({**last,'signals':hits})
        except (ValueError,OSError,TypeError):
            parse_errors.append(str(path.relative_to(work)))
    stop_text=(str(info.get('exit_status',''))+' '+stderr).casefold()
    stop_signals=[name for name,phrases in PATTERNS.items() if any(p in stop_text for p in phrases)]
    if code==124 and 'wall_timeout' not in stop_signals:stop_signals.append('wall_timeout')
    return {'process_exit':code,'trajectory_status':info.get('exit_status'),
            'runner_error_type':runner_error,'last_saved_step':last,'saved_step_count':len(paths),
            'signal_counts':dict(counts),'stop_signals':stop_signals,'error_events':events,
            'unreadable_step_files':parse_errors,
            'screenshots':[str(p.relative_to(work)) for p in work.rglob('*.png') if p.is_file() and not p.is_symlink()],
            'trajectory_present':(work/'trajectory.json').is_file(),
            'raw_responses_present':(work/'raw_responses.jsonl').is_file(),
            'semantic_verdict':None,
            'limitation':'Text signals are diagnostic hypotheses, not proven root causes or correctness judgments. A killed in-flight step may never have been saved.'}
