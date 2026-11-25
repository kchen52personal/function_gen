#!/usr/bin/env python3
"""Posthoc trial of a previously deduplicated candidate, not a suite score."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from run_verified_site import ROOT, attempt_product, distinct, repeated_attempt, write


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if ROOT not in output.parents:
        parser.error('output must be inside the project')
    source = ROOT / 'automated_runs/codex_v69_20261110_continue_v68'
    summary = json.loads((source / 'summary.json').read_text(encoding='utf-8'))
    proposal = json.loads((source / 'round_02/screened_candidates.json').read_text(encoding='utf-8'))
    matches = [c for c in proposal['tools'] if c['name'] == 'get_contents_section_header']
    if len(matches) != 1:
        raise ValueError('expected the saved Contents heading candidate')
    candidate = matches[0]
    if not distinct(candidate, summary['accepted']) or repeated_attempt(candidate, summary['attempted_candidates']):
        raise ValueError('candidate is still blocked by current deduplication')
    recon = json.loads((source / 'recon/recon.json').read_text(encoding='utf-8'))
    output.mkdir(parents=True, exist_ok=False)
    for name in ('.tmp', '.cache', '.config'):
        (output / name).mkdir()
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
    result = {'kind': 'posthoc_previously_deduplicated_candidate_probe', 'status': 'running',
              'source': str(source), 'candidate': candidate,
              'limitations': ['Candidate selected after inspecting a deduplication rejection.',
                              'This is not a fresh suite result or an unbiased comparison.',
                              'Different cited pages do not alone prove distinct information.',
                              'The generated reference shares cited page evidence.']}
    write(output / 'summary.json', result)
    attempt = attempt_product(summary['url'], candidate, recon, output / 'version_00', env,
                              '', 3, None, experimental_reference=True)
    result.update(status='complete', attempt=attempt)
    write(output / 'summary.json', result)
    print(json.dumps({'status': result['status'], 'attempt_status': attempt.get('status'),
                      'reason': attempt.get('reason'), 'output': str(output),
                      'limitations': result['limitations']}, indent=2))


if __name__ == '__main__':
    main()
