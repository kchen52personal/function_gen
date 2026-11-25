#!/usr/bin/env python3
"""Posthoc scope clarification and new reference; not a fresh-suite score."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path

from result_log import read_result
from run_verified_site import ROOT, _run, write
from uniform_reference_agent import run as reference_run
from uniform_reference_check import validate_tool_output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if ROOT not in output.parents:
        parser.error('output must be inside the project')
    source = ROOT / 'automated_runs/codex_v66_20261109_continue_v65'
    summary = json.loads((source / 'summary.json').read_text(encoding='utf-8'))
    entries = [x for x in summary['accepted'] if x['candidate']['name'] == 'get_article_title_from_content']
    if len(entries) != 1:
        raise ValueError('expected the saved article-title attempt')
    entry = entries[0]
    script = Path(entry['script']).resolve(strict=True)
    if ROOT not in script.parents or hashlib.sha256(script.read_bytes()).hexdigest() != entry.get('script_sha256'):
        raise ValueError('saved script integrity mismatch')
    candidate = copy.deepcopy(entry['candidate'])
    # Scope selected after observing the existing script and live page.
    candidate['goal'] = 'Retrieve one recently featured article title by zero-based position from the Main Page'
    candidate['success_contract'] = ('returns the article title at the specified zero-based position in '
                                     'the Recently featured list beneath the featured article')
    candidate['boundary'] = ('only the Recently featured list; fail if the requested position is unavailable; '
                             'does not return article content or other section headings')
    version = source / 'attempt_03/version_00'
    labels = ('default', 'held', 'stress_boundary')
    outputs = {label: read_result(version / 'agent/final_runs' / label / 'final_script_log.txt') for label in labels}
    cases = {label: outputs[label]['parameters'] for label in labels}
    for label in labels:
        errors = validate_tool_output(summary['url'], candidate, label, outputs[label], cases[label])
        if errors:
            raise ValueError('; '.join(errors))
    grounding = json.loads((version / 'generic_grounding.json').read_text(encoding='utf-8'))
    output.mkdir(parents=True, exist_ok=False)
    for directory in ('.tmp', '.cache', '.config'):
        (output / directory).mkdir()
    env = dict(os.environ)
    env.pop('CODEX_INDEPENDENT_REFERENCE_RECON', None)
    env.update(PYTHONPATH=f'{Path(__file__).resolve().parent}:{ROOT / "vendor/Webwright/src"}',
               PLAYWRIGHT_BROWSERS_PATH=str(ROOT / '.playwright-browsers'),
               XDG_CONFIG_HOME=str(output / '.config'), XDG_CACHE_HOME=str(output / '.cache'),
               TMPDIR=str(output / '.tmp'), PYTHONDONTWRITEBYTECODE='1')
    os.environ.update(env)
    report = {'kind': 'posthoc_recent_titles_scope_probe', 'status': 'running',
              'source': str(source), 'candidate': candidate, 'script': str(script),
              'script_sha256': entry['script_sha256'], 'cases': cases,
              'limitations': ['Scope narrowed after inspecting the original outputs and page.',
                              'Not a fresh suite score, unbiased evaluation, or proof evolution caused a gain.',
                              'Reference receives shared cited evidence; hidden structural changes can break the tool.',
                              'The three cases were already observed; index 2 was disclosed during generation.']}
    write(output / 'summary.json', report)
    independent = reference_run(summary['url'], candidate, cases, outputs, grounding,
                                output / 'independent_reference', env, 6, _run, model_name='gpt-4.1')
    report.update(status='complete', reference=independent)
    write(output / 'summary.json', report)
    print(json.dumps({'status': 'complete', 'reference_status': independent.get('status'),
                      'output': str(output), 'limitations': report['limitations']}, indent=2))


if __name__ == '__main__':
    main()
