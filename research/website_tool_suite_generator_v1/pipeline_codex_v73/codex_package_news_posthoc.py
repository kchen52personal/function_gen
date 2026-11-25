#!/usr/bin/env python3
"""Package the audited v50 news diagnostic for invocation, not suite scoring."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from invoke_verified_tool import ROOT, inside_project
from result_log import read_result
from run_verified_site import write
from uniform_reference_check import VERIFIED_STATUS, VERIFICATION_VERSION


def main() -> None:
    source = ROOT / 'automated_runs/codex_v50_20261109_news_missing_list_repair'
    summary = json.loads((source / 'summary.json').read_text(encoding='utf-8'))
    if summary.get('status') != 'verified_posthoc' or not summary.get('versions'):
        raise ValueError('diagnostic was not verified')
    result = summary['versions'][-1]
    script = inside_project(Path(result['script']))
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    if (result.get('status') != 'verified' or result.get('counted') is not True
            or digest != result.get('script_sha256')
            or digest != 'c79eb4d4ca580c3c5c8838c589dfb93f181706ed2fdf18eb0909eacf64f680d0'):
        raise ValueError('script is not the independently verified, adversarially tested version')
    report = json.loads(inside_project(Path(result['verification'])).read_text(encoding='utf-8'))
    if (report.get('status') != VERIFIED_STATUS or report.get('counts_as_verified') is not True
            or report.get('verification_version') != VERIFICATION_VERSION):
        raise ValueError('missing current independent certificate')
    invalid = result.get('invalid_input_cases', {})
    if set(invalid) != {'below_minimum', 'above_maximum'} or any(
            item.get('exit_code') == 0 for item in invalid.values()):
        raise ValueError('invalid-boundary tests did not both fail')
    fixture = ROOT / 'automated_runs/codex_v50_20261109_news_adversarial_v00'
    for name in ('short', 'absent'):
        if not (fixture / name / 'final_script_log.txt').read_text(encoding='utf-8').rstrip().endswith('final result: error'):
            raise ValueError(f'{name} regression did not fail closed')
    valid = read_result(fixture / 'valid/final_script_log.txt')
    nested = read_result(fixture / 'nested/final_script_log.txt')
    default = read_result(source / 'version_00/agent/final_runs/default/final_script_log.txt')
    second = read_result(source / 'version_00/agent/final_runs/secondary_held/final_script_log.txt')
    if (valid['parameters'] != {'position': 1} or nested['parameters'] != {'position': 2}
            or valid['result'] != default['result'] or nested['result'] != second['result']
            or 'SYNTHETIC NESTED NOTE' in json.dumps(nested['result'])):
        raise ValueError('normal/nested regression output is wrong')
    destination = source / 'summary_codex_posthoc.json'
    if destination.exists():
        raise FileExistsError('refusing to overwrite an existing posthoc manifest')
    manifest = {'url': summary['url'], 'pipeline_policy': 'codex_v50_missing_list_tte',
                'status': 'verified_posthoc_not_suite_score',
                'source_diagnostic': str(source / 'summary.json'),
                'limitations': ['Task and repair feedback were selected after observing failures.',
                                'Adversarial fixtures were repair-informed regression checks.',
                                'Independent verification covers only the recorded live cases.',
                                'Page structure and headline availability can change.'],
                'accepted': [{'candidate': summary['candidate'], **result}]}
    write(destination, manifest)
    print(f'Wrote posthoc invocation manifest: {destination}')


if __name__ == '__main__':
    main()
