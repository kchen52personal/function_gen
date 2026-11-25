"""Export selected recorded request bodies without provider responses or credentials."""
import ast
import hashlib
import json
from pathlib import Path
import re


RUNS = Path('/shared/hans/research/website_tool_suite_generator_v1/automated_runs')
OUT = Path('/shared/hans/research/website_automated_functions_20261125/prompts')
CONFIGS = [
    ('arxiv', 'arxiv_search_8dev4held_20261118_v2', 'tasks/proposal_1', 'task_generation'),
    ('arxiv', 'arxiv_search_8dev4held_20261118_v2', 'checkpoints/checkpoint_08/synthesis', 'synthesis'),
    ('youdao_create', 'youdao_new_item_zh_7dev3held_20261120_static_v1', 'tasks', 'task_generation'),
    ('youdao_create', 'youdao_new_item_zh_7dev3held_20261120_static_v1', 'replacements', 'replacement_generation'),
    ('youdao_create', 'youdao_new_item_zh_7dev3held_20261120_static_v1', 'checkpoints/checkpoint_07/synthesis', 'synthesis'),
    ('youdao_open', 'youdao_open_named_7dev3held_20261124_v1', 'tasks', 'task_generation'),
    ('youdao_open', 'youdao_open_named_7dev3held_20261124_v1', 'replacements', 'replacement_generation'),
    ('youdao_open', 'youdao_open_named_7dev3held_20261124_v1', 'checkpoints/checkpoint_07/synthesis', 'synthesis'),
    ('govuk', 'codex_v73_20261110_govuk_fresh_bounded', 'attempt_01/version_00/api_logs', 'generator'),
]
LOCAL_PATH = re.compile(r'/(?:shared/hans|home/[^/\s]+)[^\s\'"<>]*')
SECRET = re.compile(r'(?i)(?:sk-[A-Za-z0-9_-]{16,}|bearer\s+[A-Za-z0-9._-]{16,}|'
                    r'(?:api[_-]?key|password|authorization|access[_-]?token|cookie)'
                    r'[\'"\s:=]+[A-Za-z0-9_./+\-=]{12,})')


def main():
    pending = []
    for site, run, subtree, stage in CONFIGS:
        base = RUNS / run / subtree
        files = sorted(base.rglob('api_logs/calls/**/*.jsonl'))
        if stage == 'generator':
            files = sorted(base.glob('calls/qwen3.5-plus/**/*.jsonl'))
        if not files:
            raise ValueError(f'No request logs found: {run}/{subtree}')
        counter = 0
        for source in files:
            for line in source.read_text(encoding='utf-8').splitlines():
                row = json.loads(line)
                request = ast.literal_eval(row['request'])
                if not isinstance(request, dict) or not isinstance(request.get('messages'), list):
                    raise ValueError(f'Unexpected request format: {source}')
                original_json = json.dumps(request, ensure_ascii=False, sort_keys=True)
                if SECRET.search(original_json) or re.search(r'data:image/[^;\s]+;base64,[A-Za-z0-9+/=]{40,}', original_json):
                    raise ValueError(f'Potential secret or image data; do not export: {source}')
                sanitized, n = LOCAL_PATH.subn('<LOCAL_WORKSPACE_PATH>', original_json)
                # Ensure the replacement has not malformed JSON or changed its structure.
                body = json.loads(sanitized)
                counter += 1
                name = f'{site}/{stage}_{counter:02d}.json'
                pending.append((name, body, {
                    'file': name, 'source_run': run,
                    'source_log': source.relative_to(RUNS / run).as_posix(),
                    'source_log_line': len([x for x in pending if x[2]['source_log'] == source.relative_to(RUNS / run).as_posix() and x[2]['source_run'] == run]) + 1,
                    'original_request_sha256': hashlib.sha256(original_json.encode()).hexdigest(),
                    'local_path_redactions': n, 'logged_success': row.get('success'),
                }))
    OUT.mkdir(mode=0o700, exist_ok=False)
    for name, body, _ in pending:
        target = OUT / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('x', encoding='utf-8') as handle:
            json.dump(body, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
    with (OUT / 'manifest.json').open('x', encoding='utf-8') as handle:
        json.dump({'scope': 'Selected task, replacement, synthesis and GOV.UK generator requests; not all agent calls',
                   'privacy': 'Local filesystem paths replaced; no responses, sessions or credentials intentionally included. Test note titles and development evidence remain.',
                   'requests': [meta for _, _, meta in pending]}, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(f'Exported {len(pending)} logged requests to {OUT}')


if __name__ == '__main__':
    main()
