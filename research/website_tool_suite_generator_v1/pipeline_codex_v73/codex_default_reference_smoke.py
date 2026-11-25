#!/usr/bin/env python3
"""Posthoc smoke of the default shared-evidence reference, not a suite score."""
from __future__ import annotations

import argparse
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
    source = ROOT / 'automated_runs/codex_v43_20261109_continue_v35'
    summary = json.loads((source / 'summary.json').read_text(encoding='utf-8'))
    attempts = [a for a in summary['attempts'] if a['candidate']['name'] == 'get_content_quality_link']
    if len(attempts) != 1 or attempts[0].get('status') != 'verified':
        raise ValueError('expected one previously verified quality-link candidate')
    attempt = attempts[0]
    version = source / 'attempt_02/version_00'
    labels = ('default', 'held')
    outputs = {label: read_result(version / 'agent/final_runs' / label / 'final_script_log.txt')
               for label in labels}
    cases = {label: outputs[label]['parameters'] for label in labels}
    grounding = json.loads((version / 'generic_grounding.json').read_text(encoding='utf-8'))
    output.mkdir(parents=True, exist_ok=False)
    for subdir in ('.tmp', '.cache', '.config'):
        (output / subdir).mkdir()
    env = dict(os.environ)
    env.pop('CODEX_INDEPENDENT_REFERENCE_RECON', None)
    env.update(PYTHONPATH=f'{Path(__file__).resolve().parent}:{ROOT / "vendor/Webwright/src"}',
               PLAYWRIGHT_BROWSERS_PATH=str(ROOT / '.playwright-browsers'),
               XDG_CONFIG_HOME=str(output / '.config'), XDG_CACHE_HOME=str(output / '.cache'),
               TMPDIR=str(output / '.tmp'), PYTHONDONTWRITEBYTECODE='1')
    os.environ.update(env)
    result = {'kind': 'posthoc_default_reference_smoke', 'status': 'running', 'source': str(source),
              'limitations': ['Selected after an earlier success; not an unbiased benchmark.',
                              'The reference receives shared cited evidence and can share its mistakes.',
                              'A pass is not a fresh suite score.']}
    write(output / 'summary.json', result)
    report = reference_run(summary['url'], attempt['candidate'], cases, outputs, grounding,
                           output / 'independent_reference', env, 6, _run, model_name='gpt-4.1')
    result.update(status='complete', report=report)
    write(output / 'summary.json', result)
    print(json.dumps({'status': 'complete', 'reference_status': report.get('status'),
                      'errors': report.get('errors') or report.get('reason'), 'output': str(output),
                      'limitations': result['limitations']}, indent=2))


if __name__ == '__main__':
    main()
