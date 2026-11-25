#!/usr/bin/env python3
"""Posthoc, manually failure-informed navigation repair; not a suite score."""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path

from run_verified_site import ROOT, attempt_product, evolvable_failure, write
from select_candidates import goal_schema_mismatches, integer_range_coverage_errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if ROOT not in output.parents:
        parser.error('output must be inside the project')
    source = ROOT / 'automated_runs/codex_v64_20261109_continue_v57'
    old = json.loads((source / 'summary.json').read_text(encoding='utf-8'))
    candidates = [x for x in old['attempts'] if x['candidate']['name'] == 'get_site_navigation']
    if len(candidates) != 1 or candidates[0]['status'] != 'execution_failed':
        raise ValueError('expected the recorded failed navigation attempt')
    candidate = copy.deepcopy(candidates[0]['candidate'])
    errors = goal_schema_mismatches(candidate) + integer_range_coverage_errors(candidate)
    if errors:
        raise ValueError('; '.join(errors))
    baseline = source / 'attempt_01/version_01'
    prior_script = (baseline / 'agent/final_script.py').read_text(encoding='utf-8')[:20000]
    prior_stderr = (baseline / 'execution_default.stderr.txt').read_text(encoding='utf-8')[-7000:]
    feedback = (
        'Posthoc failure-informed public-default repair. Both earlier versions failed because '
        'the primary navigation link was hidden. In a separate manual live-page inspection, '
        'the visible Main menu control was an input checkbox with role="button" and '
        'id="vector-main-menu-dropdown-checkbox", not a button element; clicking the '
        'checkbox revealed the navigation link. Reinspect the live page and implement '
        'the stated contract; the page may change. This observation is public failure '
        'feedback, not a hidden test or a reference output.\n\nPrevious failure stderr:\n'
        + prior_stderr + '\n\nPrevious script:\n' + prior_script)
    recon = json.loads((source / 'recon/recon.json').read_text(encoding='utf-8'))
    output.mkdir(parents=True, exist_ok=False)
    for subdir in ('.tmp', '.cache', '.config'):
        (output / subdir).mkdir()
    env = dict(os.environ)
    env.pop('CODEX_INDEPENDENT_REFERENCE_RECON', None)
    env.pop('CODEX_VISIBLE_REFERENCE_REPAIR', None)
    env.pop('CODEX_VISIBLE_REFERENCE_VALUES', None)
    env.update(PYTHONPATH=f'{Path(__file__).resolve().parent}:{ROOT / "vendor/Webwright/src"}',
               PLAYWRIGHT_BROWSERS_PATH=str(ROOT / '.playwright-browsers'),
               XDG_CONFIG_HOME=str(output / '.config'), XDG_CACHE_HOME=str(output / '.cache'),
               TMPDIR=str(output / '.tmp'), PYTHONDONTWRITEBYTECODE='1',
               CODEX_REFERENCE_MAX_CALLS='6', CODEX_GENERATOR_MODEL='qwen3.5-plus')
    os.environ.update(env)
    summary = {'kind': 'posthoc_failure_informed_navigation_repair', 'url': old['url'],
               'source': str(source), 'candidate': candidate, 'status': 'running', 'versions': [],
               'limitations': ['Manual live-page inspection supplied the failure feedback.',
                               'Posthoc diagnostics are not a fresh suite or unbiased comparison.',
                               'The separately generated reference shares cited evidence.',
                               'The site and browser-specific shortcut formatting can change.']}
    write(output / 'summary.json', summary)
    for version in range(2):
        result = attempt_product(summary['url'], candidate, recon, output / f'version_{version:02d}',
                                 env, feedback, 3, None, experimental_reference=True)
        summary['versions'].append(result)
        write(output / 'summary.json', summary)
        if not evolvable_failure(result) or version == 1:
            break
        feedback = ('The public-example repair still failed. Reinspect the live page and the '
                    'stated contract; no hidden result is supplied.\n' + result['repair_context'])[-30000:]
    summary['status'] = 'verified_posthoc' if summary['versions'][-1].get('counted') is True else 'not_verified_posthoc'
    write(output / 'summary.json', summary)
    print(json.dumps({'status': summary['status'], 'versions': [v.get('status') for v in summary['versions']],
                      'output': str(output), 'limitations': summary['limitations']}, indent=2))


if __name__ == '__main__':
    main()
