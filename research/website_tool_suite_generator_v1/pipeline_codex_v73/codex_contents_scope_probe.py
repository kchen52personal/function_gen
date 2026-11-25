#!/usr/bin/env python3
"""Retest a posthoc clarified heading scope; not a fresh-suite score."""
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if ROOT not in output.parents:
        parser.error('output must be inside the project')
    source = ROOT / 'automated_runs/codex_v70_20261110_distinct_page_probe'
    summary = json.loads((source / 'summary.json').read_text(encoding='utf-8'))
    attempt = summary.get('attempt', {})
    if summary.get('status') != 'complete' or attempt.get('status') != 'verification_failed':
        raise ValueError('expected the completed prior reference failure')
    script = Path(attempt['script']).resolve(strict=True)
    if ROOT not in script.parents or hashlib.sha256(script.read_bytes()).hexdigest() != attempt.get('script_sha256'):
        raise ValueError('saved script integrity mismatch')
    candidate = copy.deepcopy(summary['candidate'])
    # Clarified after seeing the actual tool, page, and reference failure.
    candidate['goal'] = 'Retrieve a top-level content section heading on the Wikipedia Contents page by zero-based position'
    candidate['success_contract'] = ('Return the text of the requested h2 heading within the page main content, '
                                     'in document order; do not use the sidebar table of contents or h3 subsections.')
    candidate['boundary'] = 'Return heading text only; do not retrieve section content, links, or subsection headings.'
    version = source / 'version_00'
    labels = ('default', 'held', 'stress_boundary', 'secondary_held')
    outputs = {label: read_result(version / 'agent/final_runs' / label / 'final_script_log.txt') for label in labels}
    cases = {label: outputs[label]['parameters'] for label in labels}
    grounding = json.loads((version / 'generic_grounding.json').read_text(encoding='utf-8'))
    output.mkdir(parents=True, exist_ok=False)
    for name in ('.tmp', '.cache', '.config'):
        (output / name).mkdir()
    env = dict(os.environ)
    env.pop('CODEX_INDEPENDENT_REFERENCE_RECON', None)
    env.update(PYTHONPATH=f'{Path(__file__).resolve().parent}:{ROOT / "vendor/Webwright/src"}',
               PLAYWRIGHT_BROWSERS_PATH=str(ROOT / '.playwright-browsers'),
               XDG_CONFIG_HOME=str(output / '.config'), XDG_CACHE_HOME=str(output / '.cache'),
               TMPDIR=str(output / '.tmp'), PYTHONDONTWRITEBYTECODE='1')
    os.environ.update(env)
    report = {'kind': 'posthoc_clarified_contents_heading_scope', 'status': 'running',
              'source': str(source), 'candidate': candidate, 'script': str(script),
              'script_sha256': attempt['script_sha256'], 'cases': cases,
              'limitations': ['Scope clarified after seeing the implementation, page, and reference failure.',
                              'Not a fresh suite score, unbiased evaluation, or proof evolution caused improvement.',
                              'The generated reference shares cited page evidence; finite cases are not complete coverage.',
                              'The initial independent reference failed; this is a posthoc retry.']}
    write(output / 'summary.json', report)
    reference = reference_run('https://en.wikipedia.org/', candidate, cases, outputs, grounding,
                              output / 'independent_reference', env, 6, _run, model_name='gpt-4.1')
    report.update(status='complete', reference=reference)
    write(output / 'summary.json', report)
    print(json.dumps({'status': 'complete', 'reference_status': reference.get('status'),
                      'reason': reference.get('reason') or reference.get('errors'),
                      'output': str(output), 'limitations': report['limitations']}, indent=2))


if __name__ == '__main__':
    main()
