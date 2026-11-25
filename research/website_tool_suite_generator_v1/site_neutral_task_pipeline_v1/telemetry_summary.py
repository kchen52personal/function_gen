"""Summarize metadata-only timing events without reading prompts or page data."""
from collections import Counter, defaultdict
import json
from pathlib import Path


def _load(path):
    path = Path(path)
    if not path.is_file():
        return [], False, 0
    events, bad = [], 0
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError('not an object')
            events.append(value)
        except (ValueError, TypeError):
            bad += 1
    return events, True, bad


def _union(intervals):
    merged = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return round(sum((end-start)/1e9 for start, end in merged), 3)


def _pairs(events, start_name, finish_name, stage=None):
    starts = {}
    durations, intervals = [], []
    finished = set()
    for row in events:
        if stage is not None and row.get('stage') != stage:
            continue
        op, stamp = row.get('operation_id'), row.get('monotonic_ns')
        if not isinstance(op, str) or not isinstance(stamp, int):
            continue
        if row.get('event') == start_name:
            starts[op] = stamp
        elif row.get('event') == finish_name and op in starts and stamp >= starts[op]:
            durations.append((stamp-starts[op])/1e9)
            intervals.append((starts[op], stamp))
            finished.add(op)
    return {'completed': len(durations), 'started_without_finish': len(set(starts)-finished),
            'sum_seconds': round(sum(durations), 3), 'union_seconds': _union(intervals)}, intervals


def summarize(root, wall_seconds):
    root = Path(root)
    command, have_command, bad_command = _load(root/'command_events.jsonl')
    model, have_model, bad_model = _load(root/'model_events.jsonl')
    network, have_network, bad_network = _load(root/'network.jsonl')
    timings = {}
    for label, start, finish in (('step','step_started','step_finished'),
                                  ('command','command_started','command_finished'),
                                  ('smoke','smoke_check_started','smoke_check_finished')):
        timings[label], _ = _pairs(command, start, finish)
    model_intervals = []
    for label, stage in (('main_model', 'webwright_model'), ('inner_model', 'webwright_inner_model')):
        timings[label], intervals = _pairs(model, 'request_started', 'request_finished', stage)
        model_intervals.extend(intervals)
    by_model = defaultdict(lambda: {'finished': 0, 'usage_reported': 0, 'usage_unreported': 0,
                                    'reported_input_tokens': 0, 'reported_output_tokens': 0})
    for row in model:
        if row.get('event') != 'request_finished':
            continue
        name = row.get('returned_model') or row.get('requested_model') or 'unknown'
        bucket = by_model[name]
        bucket['finished'] += 1
        a, b = row.get('input_tokens'), row.get('output_tokens')
        if type(a) is int and type(b) is int:
            bucket['usage_reported'] += 1
            bucket['reported_input_tokens'] += a
            bucket['reported_output_tokens'] += b
        else:
            bucket['usage_unreported'] += 1
    return {
        'wall_seconds': round(wall_seconds, 3),
        'available': {'command_events': have_command, 'model_events': have_model, 'network_events': have_network},
        'malformed_rows': {'command': bad_command, 'model': bad_model, 'network': bad_network},
        'timings': timings, 'any_logged_model_active_union_seconds': _union(model_intervals),
        'smoke_outcomes': dict(Counter(row.get('status', 'unknown') for row in command
                                       if row.get('event') == 'smoke_check_finished')),
        'model_finish_statuses': dict(Counter(row.get('status', 'unknown') for row in model
                                               if row.get('event') == 'request_finished')),
        'model_usage': dict(by_model),
        'network_event_statuses': dict(Counter(row.get('status', 'unknown') for row in network)),
        'network_failure_reasons': dict(Counter(row.get('reason') or 'unknown' for row in network
                                                 if row.get('status') == 'denied_or_failed')),
        'limitation': ('Timing categories overlap and must not be summed. Missing, interrupted, or unlogged '
                       'operations and unreported token usage are unknown. Process/smoke status does not '
                       'prove semantic task success. Website HTTP content and model-provider internals are not observed.'),
    }
