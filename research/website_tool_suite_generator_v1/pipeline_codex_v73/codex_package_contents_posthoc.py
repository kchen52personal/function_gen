#!/usr/bin/env python3
"""Package the posthoc clarified Contents heading probe, never a suite score."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from invoke_verified_tool import ROOT, inside_project
from result_log import read_result
from run_verified_site import POLICY_VERSION, write
from uniform_reference_check import VERIFIED_STATUS, VERIFICATION_VERSION


def main() -> None:
    scope = ROOT / 'automated_runs/codex_v70_20261110_contents_scope_probe'
    source = ROOT / 'automated_runs/codex_v70_20261110_distinct_page_probe'
    data = json.loads((scope / 'summary.json').read_text(encoding='utf-8'))
    original = json.loads((source / 'summary.json').read_text(encoding='utf-8'))
    reference = data.get('reference', {})
    if (data.get('status') != 'complete' or reference.get('status') != VERIFIED_STATUS
            or reference.get('counts_as_verified') is not True
            or reference.get('verification_version') != VERIFICATION_VERSION):
        raise ValueError('clarified scope lacks a successful reference comparison')
    script = inside_project(Path(data['script']))
    if hashlib.sha256(script.read_bytes()).hexdigest() != data.get('script_sha256'):
        raise ValueError('script integrity mismatch')
    invalid = original.get('attempt', {}).get('invalid_input_cases', {})
    if set(invalid) != {'below_minimum', 'above_maximum'} or any(
            case.get('exit_code') == 0 for case in invalid.values()):
        raise ValueError('both invalid bounds were not rejected')
    # Manual live DOM inspection established this ordered top-level list on
    # 2026-11-10. Four cases were in the reference comparison; four more are
    # posthoc live checks. These observations are not unseen tests.
    expected = ['Browse by subject', 'Browse by format', 'Articles by quality',
                'Spoken articles', 'Alphabetical lists of articles', 'Topics',
                'Types', 'Places, people and times']
    workspace = source / 'version_00/agent'
    paths = {0: workspace / 'final_runs/default', 4: workspace / 'final_runs/held',
             6: workspace / 'final_runs/secondary_held', 7: workspace / 'final_runs/stress_boundary',
             1: workspace / 'codex_scope_extra_1', 2: workspace / 'codex_scope_extra_2_retry',
             3: workspace / 'codex_scope_extra_3', 5: workspace / 'codex_scope_extra_5'}
    for index, directory in paths.items():
        observed = read_result(directory / 'final_script_log.txt')
        if (observed.get('parameters') != {'section_index': index}
                or observed.get('result') != {'header_text': expected[index]}):
            raise ValueError(f'index {index} did not match the recorded live DOM')
    certificate = scope / 'independent_reference_summary_codex_posthoc.json'
    manifest = scope / 'summary_codex_posthoc.json'
    if certificate.exists() or manifest.exists():
        raise FileExistsError('refusing to overwrite an existing posthoc artifact')
    write(certificate, reference)
    entry = {'candidate': data['candidate'], 'status': 'verified', 'counted': True,
             'script': str(script), 'script_sha256': data['script_sha256'],
             'verification': str(certificate),
             'reason': 'posthoc clarified scope and separately generated reference on recorded cases'}
    write(manifest, {'url': 'https://en.wikipedia.org/', 'pipeline_policy': POLICY_VERSION,
                     'status': 'verified_posthoc_not_suite_score',
                     'source_diagnostic': str(scope / 'summary.json'),
                     'limitations': data['limitations'] + [
                         'Four additional indices were compared with a manually inspected live DOM.',
                         'Heading structure and visibility can change; the script does not filter hidden elements.'],
                     'accepted': [entry]})
    print(f'Wrote posthoc invocation manifest: {manifest}')


if __name__ == '__main__':
    main()
