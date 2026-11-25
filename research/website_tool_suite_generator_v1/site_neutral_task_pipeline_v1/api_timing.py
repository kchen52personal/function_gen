"""Summarize completed logged API calls without assuming their times are additive."""
import json
from pathlib import Path
import statistics

def summarize(stats_root: Path):
    groups = {'main_agent':[], 'inner_tools':[]}
    malformed = 0
    for path in stats_root.rglob('*.jsonl') if stats_root.exists() else ():
        if path.name.startswith('hans_webwright_inner_tool_'):
            group = 'inner_tools'
        elif path.name.startswith('hans_website_tool_webwright_'):
            group = 'main_agent'
        else:
            continue
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
                if not isinstance(row,dict): raise ValueError('invalid stats row')
                groups[group].append(row)
            except (ValueError,TypeError):
                malformed += 1
    result = {}
    for name, rows in groups.items():
        seconds = [r['duration_ms']/1000 for r in rows
                   if isinstance(r.get('duration_ms'),(float,int)) and not isinstance(r['duration_ms'],bool)]
        result[name] = {'logged_calls':len(rows), 'timed_calls':len(seconds),
            'successful_logged_calls':sum(r.get('success') is True for r in rows),
            'failed_logged_calls':sum(r.get('success') is False for r in rows),
            'sum_call_seconds':round(sum(seconds),2),
            'median_call_seconds':round(statistics.median(seconds),2) if seconds else None,
            'max_call_seconds':round(max(seconds),2) if seconds else None,
            'logged_input_tokens':sum(r.get('prompt_tokens',0) or 0 for r in rows),
            'logged_output_tokens':sum(r.get('completion_tokens',0) or 0 for r in rows)}
    result['malformed_log_rows'] = malformed
    result['limitation'] = ('Completed logged request durations can overlap and do not add to wall time; '
        'interrupted or unlogged requests and provider-side queue/network breakdown may be missing.')
    return result
